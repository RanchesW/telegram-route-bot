import os
import logging
import json
import uuid
import datetime
import googlemaps
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler, CallbackQueryHandler, filters
)
from telegram import (
    ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, Update
)
from telegram.constants import ParseMode
from dotenv import load_dotenv
from cryptography.fernet import Fernet

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    filename='bot_activity.log',
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Получение API ключей
api_key = os.environ.get('GOOGLE_MAPS_API_KEY') or 'YOUR_GOOGLE_MAPS_API_KEY'
telegram_bot_token = os.environ.get('TELEGRAM_BOT_TOKEN') or 'YOUR_TELEGRAM_BOT_TOKEN'

if not api_key:
    raise ValueError("Необходимо установить переменную окружения GOOGLE_MAPS_API_KEY.")

if not telegram_bot_token:
    raise ValueError("Необходимо установить переменную окружения TELEGRAM_BOT_TOKEN.")

# Инициализация клиента Google Maps
gmaps = googlemaps.Client(key=api_key)

# Генерация или загрузка ключа шифрования
ENCRYPTION_KEY_FILE = 'encryption_key.key'
if os.path.exists(ENCRYPTION_KEY_FILE):
    with open(ENCRYPTION_KEY_FILE, 'rb') as f:
        encryption_key = f.read()
else:
    encryption_key = Fernet.generate_key()
    with open(ENCRYPTION_KEY_FILE, 'wb') as f:
        f.write(encryption_key)
cipher_suite = Fernet(encryption_key)

# Определение состояний
(
    CHOOSING_ROLE,
    WAITING_FOR_LOCATION,
    ENTERING_PASSWORD,
    CONTACTING_SUPPORT,
    SELECTING_PRIORITY,
    BROADCASTING,
    ADDING_USER,
    REMOVING_USER,
    VIEWING_TICKET_DETAILS,
    TICKET_ACTION,
    REPLYING_TO_TICKET,
    POST_REPLY_ACTION,
    CHANGING_TICKET_STATUS,
    VIEWING_ROUTE_DETAILS,
    EDITING_ROUTE,
    SHARING_LOCATION
) = range(16)

# Локация рабочего места (широта, долгота)
workplace_location = "51.155406,71.4101"

# Пароли для ролей
ROLE_PASSWORDS = {
    'администратор': '',  # Замените на ваш пароль администратора
    'водитель': '',      # Замените на пароль для водителей
    'пассажир': ''    # Замените на пароль для пассажиров
}

# ID главного администратора
MAIN_ADMIN_ID =   # Замените на ваш Telegram ID

# Файл для хранения белого списка
WHITELIST_FILE = 'whitelist.json'

# Файл для хранения тикетов поддержки
TICKETS_FILE = 'tickets.json'

# Возможные статусы и приоритеты тикетов
TICKET_STATUSES = ['Ожидает ответа', 'В работе', 'Закрыт']
TICKET_PRIORITIES = ['Низкий', 'Средний', 'Высокий']

# Функции для работы с белым списком
def load_whitelist():
    try:
        with open(WHITELIST_FILE, 'r') as f:
            return set(json.load(f))
    except FileNotFoundError:
        with open(WHITELIST_FILE, 'w') as f:
            json.dump([], f)
        return set()
    except json.JSONDecodeError:
        return set()

def save_whitelist(whitelist):
    with open(WHITELIST_FILE, 'w') as f:
        json.dump(list(whitelist), f)

# Инициализация белого списка
whitelist = load_whitelist()

# Функции для работы с тикетами поддержки
def load_tickets():
    try:
        with open(TICKETS_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        with open(TICKETS_FILE, 'w') as f:
            json.dump([], f)
        return []
    except json.JSONDecodeError:
        return []

def save_tickets(tickets):
    with open(TICKETS_FILE, 'w') as f:
        json.dump(tickets, f)

# Загрузка тикетов
tickets = load_tickets()

# Функция шифрования данных
def encrypt_data(data):
    if data:
        return cipher_suite.encrypt(data.encode()).decode()
    return None

def decrypt_data(encrypted_data):
    if encrypted_data:
        return cipher_suite.decrypt(encrypted_data.encode()).decode()
    return None

# Класс для представления маршрута
class Route:
    def __init__(self, driver_id, origin):
        self.driver_id = driver_id
        self.origin = encrypt_data(origin)
        self.current_location = None  # Текущее местоположение водителя
        self.pickup_locations = []
        self.passenger_ids = []
        self.is_open = True
        self.eta = {}  # ETA к каждой точке
        self.notified_passengers = set()  # Пассажиры, которым отправлено уведомление
        self.pickup_order = []  # Новый список для хранения порядка остановок
        self.next_passenger_index = 0  # Индекс следующего пассажира для уведомления

# Глобальный словарь маршрутов
routes = {}

# Функция для оптимизации маршрута с помощью Google Maps API
def optimize_route(origin, destination, pickup_locations):
    try:
        waypoints = [decrypt_data(loc) for loc in pickup_locations]
        directions_result = gmaps.directions(
            origin=decrypt_data(origin),
            destination=destination,
            waypoints=waypoints,
            optimize_waypoints=True
        )

        if directions_result and len(directions_result) > 0:
            route = directions_result[0]
            optimized_order = route['waypoint_order']
            optimized_waypoints = [pickup_locations[i] for i in optimized_order]
            total_duration = sum(leg['duration']['value'] for leg in route['legs'])
            return optimized_waypoints, total_duration
        else:
            logger.error("Не удалось получить направления от Google Maps API.")
            return pickup_locations, None
    except Exception as e:
        logger.exception("Ошибка при оптимизации маршрута")
        return pickup_locations, None

def optimize_route_with_order(origin, destination, pickup_locations):
    try:
        waypoints = [decrypt_data(loc) for loc in pickup_locations]
        directions_result = gmaps.directions(
            origin=decrypt_data(origin),
            destination=destination,
            waypoints=waypoints,
            optimize_waypoints=True
        )

        if directions_result and len(directions_result) > 0:
            route = directions_result[0]
            optimized_order = route['waypoint_order']  # Индексы оптимизированного порядка
            optimized_waypoints = [pickup_locations[i] for i in optimized_order]
            total_duration = sum(leg['duration']['value'] for leg in route['legs'])
            return optimized_waypoints, total_duration, optimized_order
        else:
            logger.error("Не удалось получить направления от Google Maps API.")
            return pickup_locations, None, list(range(len(pickup_locations)))
    except Exception as e:
        logger.exception("Ошибка при оптимизации маршрута")
        return pickup_locations, None, list(range(len(pickup_locations)))


# Функция для генерации ссылки на Яндекс.Карты с оптимизированными точками
def generate_yandex_maps_link(origin, destination, pickup_locations):
    points = [decrypt_data(origin)] + [decrypt_data(loc) for loc in pickup_locations] + [destination]
    points_formatted = [point.replace(',', '%2C') for point in points]
    points_str = '~'.join(points_formatted)
    link = f"https://yandex.ru/maps/?rtext={points_str}&rtt=auto"
    return link

# Функция для получения координат из адреса
def get_coordinates(address):
    try:
        geocode_result = gmaps.geocode(address)
        if geocode_result and len(geocode_result) > 0:
            location = geocode_result[0]['geometry']['location']
            latitude = location['lat']
            longitude = location['lng']
            return f"{latitude},{longitude}"
        else:
            return None
    except Exception as e:
        logger.exception("Ошибка при получении координат из адреса")
        return None

# Функция логирования действий
def log_action(user_id, action):
    logger.info(f"Пользователь {user_id}: {action}")

# Обработчики команд

# Команда /start
async def start(update, context):
    user_id = update.effective_user.id
    if user_id != MAIN_ADMIN_ID and user_id not in whitelist:
        await update.message.reply_text("Вы не имеете доступа к боту. Обратитесь к администратору для получения доступа.")
        return ConversationHandler.END

    is_authorized = context.user_data.get('is_authorized', False)
    if not is_authorized:
        await login(update, context)
        return CHOOSING_ROLE

    user_role = context.user_data.get('role')
    if user_role == 'водитель':
        await update.message.reply_text(
            "Пожалуйста, отправьте ваше местоположение или введите адрес для начала маршрута.",
            reply_markup=ReplyKeyboardRemove()
        )
        return WAITING_FOR_LOCATION
    elif user_role == 'пассажир':
        await update.message.reply_text(
            "Пожалуйста, отправьте ваше местоположение или введите адрес для присоединения к маршруту.",
            reply_markup=ReplyKeyboardRemove()
        )
        return WAITING_FOR_LOCATION
    elif user_role == 'администратор':
        await update.message.reply_text(
            "Вы вошли как администратор. Используйте команды для управления ботом.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

# Авторизация пользователя
async def login(update, context):
    user_id = update.effective_user.id
    if user_id != MAIN_ADMIN_ID and user_id not in whitelist:
        await update.message.reply_text("Вы не имеете доступа к боту. Обратитесь к администратору для получения доступа.")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("Админ", callback_data='role_администратор')],
        [InlineKeyboardButton("Водитель", callback_data='role_водитель')],
        [InlineKeyboardButton("Пассажир", callback_data='role_пассажир')],
        [InlineKeyboardButton("Написать поддержке", callback_data='role_support')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Выберите роль, под которой хотите войти:",
        reply_markup=reply_markup
    )
    return CHOOSING_ROLE

# Выбор роли и запрос пароля
async def choose_role_login(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == 'role_support':
        await query.edit_message_text(
            "Пожалуйста, опишите вашу проблему, и мы свяжемся с вами как можно скорее.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_login')]])
        )
        return CONTACTING_SUPPORT
    else:
        role = data.split('_')[1]
        if role not in ROLE_PASSWORDS:
            await query.edit_message_text("Роль введена некорректно. Попробуйте снова.")
            return CHOOSING_ROLE
        context.user_data['role'] = role
        await query.edit_message_text(
            f"Введите пароль для роли '{role}':",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_login')]])
        )
        return ENTERING_PASSWORD

# Обработка нажатия "Назад"
async def back_to_login(update, context):
    await login(update, context)
    return CHOOSING_ROLE

# Проверка пароля и завершение авторизации
async def check_password(update, context):
    user_input = update.message.text
    if user_input.lower() == 'назад':
        return await login(update, context)
    else:
        user_role = context.user_data.get('role')
        password = user_input
        if password == ROLE_PASSWORDS[user_role]:
            context.user_data['is_authorized'] = True
            user_id = update.effective_user.id
            if user_id == MAIN_ADMIN_ID and user_role == 'администратор':
                context.user_data['is_main_admin'] = True
                await update.message.reply_text("Вы успешно вошли как главный администратор!", reply_markup=ReplyKeyboardRemove())
            else:
                context.user_data['is_main_admin'] = False
                await update.message.reply_text(f"Вы успешно вошли как '{user_role}'.", reply_markup=ReplyKeyboardRemove())
            log_action(user_id, f"Вошел как {user_role}")
            return await start(update, context)
        else:
            await update.message.reply_text("Неверный пароль. Попробуйте снова.")
            return ENTERING_PASSWORD

# Обработка обращения в поддержку
async def contacting_support(update, context):
    user_input = update.message.text
    if user_input.lower() == 'назад':
        return await login(update, context)
    else:
        context.user_data['support_message'] = user_input
        keyboard = [
            [InlineKeyboardButton("Низкий", callback_data='priority_Низкий')],
            [InlineKeyboardButton("Средний", callback_data='priority_Средний')],
            [InlineKeyboardButton("Высокий", callback_data='priority_Высокий')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Выберите приоритет вашего обращения:", reply_markup=reply_markup)
        return SELECTING_PRIORITY

# Выбор приоритета тикета
async def selecting_priority(update, context):
    query = update.callback_query
    await query.answer()
    priority = query.data.split('_')[1]
    user = update.effective_user
    ticket_id = str(uuid.uuid4())
    timestamp = datetime.datetime.now().isoformat()
    ticket = {
        'id': ticket_id,
        'user_id': user.id,
        'user_name': user.username,
        'message': context.user_data['support_message'],
        'timestamp': timestamp,
        'status': 'Ожидает ответа',
        'priority': priority,
        'admin_reply': None
    }
    tickets.append(ticket)
    save_tickets(tickets)
    admin_id = MAIN_ADMIN_ID
    try:
        await context.bot.send_message(
            chat_id=admin_id,
            text=f"Новое обращение в поддержку с приоритетом '{priority}'. Используйте команду /view_tickets для просмотра."
        )
        await query.edit_message_text(
            "Спасибо! Ваше сообщение отправлено в поддержку."
        )
        log_action(user.id, f"Отправил обращение в поддержку с приоритетом '{priority}'")
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление администратору: {e}")
        await query.edit_message_text(
            "Произошла ошибка при отправке вашего сообщения. Пожалуйста, попробуйте позже."
        )
    return ConversationHandler.END

# Обработка местоположения пользователя
async def waiting_for_location(update, context):
    user_id = update.effective_user.id
    if user_id != MAIN_ADMIN_ID and user_id not in whitelist:
        await update.message.reply_text("Вы не имеете доступа к боту. Обратитесь к администратору для получения доступа.")
        return ConversationHandler.END

    is_authorized = context.user_data.get('is_authorized', False)
    if not is_authorized:
        await unauthorized(update, context)
        return ConversationHandler.END

    user_role = context.user_data.get('role')
    if update.message.location:
        latitude = update.message.location.latitude
        longitude = update.message.location.longitude
        location_str = f"{latitude},{longitude}"
    elif update.message.text:
        address = update.message.text
        location_str = get_coordinates(address)
        if not location_str:
            await update.message.reply_text("Не удалось получить координаты по указанному адресу. Пожалуйста, попробуйте еще раз.")
            return WAITING_FOR_LOCATION
    else:
        await update.message.reply_text("Пожалуйста, отправьте ваше местоположение или введите адрес.")
        return WAITING_FOR_LOCATION

    if user_role == 'водитель':
        await handle_driver_location(update, context, location_str)
    elif user_role == 'пассажир':
        await handle_passenger_location(update, context, location_str)
    elif user_role == 'администратор':
        await update.message.reply_text("Администратор не может использовать эту команду.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Произошла ошибка. Попробуйте начать сначала с команды /start.")
        return ConversationHandler.END

    return ConversationHandler.END

# Обработка местоположения водителя
async def handle_driver_location(update, context, location_str):
    is_authorized = context.user_data.get('is_authorized', False)
    user_role = context.user_data.get('role')
    user_id = update.effective_user.id
    if not is_authorized or user_role != 'водитель':
        await unauthorized(update, context)
        return

    driver_id = user_id
    route = Route(driver_id=driver_id, origin=location_str)
    routes[driver_id] = route

    await update.message.reply_text(
        "Вы создали маршрут и ожидаете пассажиров.\n"
        "Когда будете готовы, отправьте команду /finish, чтобы завершить набор пассажиров и получить маршрут."
    )
    log_action(user_id, "Создал новый маршрут")

# Обработка местоположения пассажира
async def handle_passenger_location(update, context, location_str):
    is_authorized = context.user_data.get('is_authorized', False)
    user_role = context.user_data.get('role')
    user_id = update.effective_user.id
    if not is_authorized or user_role != 'пассажир':
        await unauthorized(update, context)
        return

    open_routes = [route for route in routes.values() if route.is_open]
    if not open_routes:
        await update.message.reply_text("В данный момент нет доступных маршрутов.")
        return

    route = open_routes[0]
    temp_pickup_locations = route.pickup_locations + [encrypt_data(location_str)]

    _, total_duration = optimize_route(
        origin=route.origin,
        destination=workplace_location,
        pickup_locations=temp_pickup_locations
    )

    if total_duration:
        total_duration_hours = total_duration / 3600
        if total_duration_hours > 2:
            await update.message.reply_text("К сожалению, добавление вашего местоположения увеличит время маршрута более чем до 2 часов. Вы не можете быть добавлены в этот маршрут.")
            return
        else:
            route.pickup_locations.append(encrypt_data(location_str))
            route.passenger_ids.append(user_id)
            await update.message.reply_text("Вы успешно добавлены в маршрут.")
            log_action(user_id, f"Присоединился к маршруту {route.driver_id}")
    else:
        await update.message.reply_text("Не удалось определить длительность маршрута. Пожалуйста, попробуйте позже.")

# Завершение маршрута водителем
async def finish_route(update, context):
    user_id = update.effective_user.id
    if user_id != MAIN_ADMIN_ID and user_id not in whitelist:
        await update.message.reply_text("Вы не имеете доступа к боту. Обратитесь к администратору для получения доступа.")
        return

    is_authorized = context.user_data.get('is_authorized', False)
    user_role = context.user_data.get('role')
    if not is_authorized:
        await unauthorized(update, context)
        return

    if user_role != 'водитель':
        await no_permissions(update, context)
        return

    driver_id = user_id

    if driver_id in routes:
        route = routes[driver_id]
        if route.is_open:
            route.is_open = False

            # Оптимизируем маршрут и получаем оптимизированный порядок
            optimized_pickup_locations, total_duration, waypoint_order = optimize_route_with_order(
                origin=route.origin,
                destination=workplace_location,
                pickup_locations=route.pickup_locations
            )

            # Сохраняем оптимизированные точки и порядок
            route.pickup_locations = optimized_pickup_locations
            route.pickup_order = waypoint_order
            route.next_passenger_index = 0  # Начинаем с первого пассажира в порядке

            # Переставляем passenger_ids в порядке оптимизированного маршрута
            route.passenger_ids = [route.passenger_ids[i] for i in waypoint_order]

            if total_duration:
                total_duration_hours = total_duration / 3600
                duration_str = f"Общая длительность маршрута: {total_duration_hours:.2f} часов."
            else:
                duration_str = "Не удалось определить общую длительность маршрута."

            yandex_maps_link = generate_yandex_maps_link(
                origin=route.origin,
                destination=workplace_location,
                pickup_locations=optimized_pickup_locations
            )

            # Сохраняем оптимизированные точки
            route.pickup_locations = optimized_pickup_locations

            await update.message.reply_text(
                f"Вы завершили набор пассажиров. {duration_str}\nВот ваш оптимизированный маршрут на Яндекс.Картах:\n{yandex_maps_link}\n\n"
                "Пожалуйста, поделитесь вашим живым местоположением, чтобы мы могли рассчитывать ожидаемое время прибытия."
            )

            for passenger_id in route.passenger_ids:
                try:
                    await context.bot.send_message(
                        chat_id=passenger_id,
                        text="Маршрут сформирован. Водитель скоро свяжется с вами."
                    )
                except Exception as e:
                    logger.error(f"Не удалось отправить сообщение пассажиру {passenger_id}: {e}")
            log_action(user_id, "Завершил маршрут")
        else:
            await update.message.reply_text("Вы уже завершили набор пассажиров.")
    else:
        await update.message.reply_text("У вас нет активного маршрута.")

# Обработчик для неавторизованных пользователей
async def unauthorized(update, context):
    await update.message.reply_text(
        "Вы не авторизованы. Пожалуйста, используйте команду /login для авторизации.",
        reply_markup=ReplyKeyboardRemove()
    )

# Обработчик для недостатка прав
async def no_permissions(update, context):
    await update.message.reply_text(
        "У вас нет прав для использования этой команды.",
        reply_markup=ReplyKeyboardRemove()
    )

# Обработчик для получения живого местоположения водителя
async def handle_live_location(update, context):
    user_id = update.effective_user.id
    user_role = context.user_data.get('role')

    if user_role != 'водитель':
        return  # Обрабатываем только сообщения от водителей

    current_location = None

    if update.message and update.message.location:
        # Первое сообщение с местоположением или обновление
        current_location = update.message.location
    elif update.edited_message and update.edited_message.location:
        # Обновление живого местоположения
        current_location = update.edited_message.location

    if current_location:
        route = routes.get(user_id)
        if route:
            route.current_location = f"{current_location.latitude},{current_location.longitude}"
            await update_driver_eta(route, context)
        else:
            await update.effective_chat.send_message("У вас нет активного маршрута.")
    else:
        # Игнорируем другие сообщения
        pass


# Функция для обновления ETA и уведомления пассажиров
async def update_driver_eta(route, context):
    if not route.current_location:
        return  # Нет текущего местоположения водителя

    # Проверяем, есть ли еще пассажиры для забора
    if route.next_passenger_index >= len(route.pickup_locations):
        return  # Все пассажиры уже забраны

    # Берем следующую точку пассажира
    next_pickup_location = route.pickup_locations[route.next_passenger_index]
    next_passenger_id = route.passenger_ids[route.next_passenger_index]
    next_pickup_coordinates = decrypt_data(next_pickup_location)

    # Используем Google Maps Distance Matrix API для расчета ETA до следующей точки
    try:
        distance_matrix = gmaps.distance_matrix(
            origins=[route.current_location],
            destinations=[next_pickup_coordinates],
            mode="driving",
            departure_time="now"
        )

        if distance_matrix['status'] == 'OK':
            element = distance_matrix['rows'][0]['elements'][0]
            if element['status'] == 'OK':
                eta_seconds = element['duration_in_traffic']['value']
                distance_meters = element['distance']['value']
                eta_time = datetime.datetime.now() + datetime.timedelta(seconds=eta_seconds)
                # Сохраняем ETA
                route.eta[next_pickup_coordinates] = eta_time.isoformat()

                if distance_meters <= 50:  # Расстояние менее 50 метров
                    # Считаем, что пассажир забран
                    route.next_passenger_index += 1
                    # Сбрасываем notified_passengers для следующего пассажира
                    route.notified_passengers.discard(next_passenger_id)
                    # Опционально: Уведомить водителя, что пассажир забран
                    await context.bot.send_message(
                        chat_id=route.driver_id,
                        text=f"Вы прибыли к пассажиру {next_passenger_id}. Переходим к следующему пассажиру."
                    )
                else:
                    # Проверяем, нужно ли уведомить пассажира
                    if next_passenger_id not in route.notified_passengers and eta_seconds <= 300:
                        minutes = eta_seconds // 60
                        await context.bot.send_message(
                            chat_id=next_passenger_id,
                            text=f"Водитель прибудет через {minutes} минут(ы). Пожалуйста, готовьтесь выйти."
                        )
                        route.notified_passengers.add(next_passenger_id)
            else:
                route.eta[next_pickup_coordinates] = None
        else:
            logger.error("Ошибка при получении Distance Matrix")
    except Exception as e:
        logger.exception("Ошибка при обновлении ETA")

# Команда /show_eta для водителя
async def show_eta(update, context):
    user_id = update.effective_user.id
    user_role = context.user_data.get('role')

    if user_role != 'водитель':
        await update.message.reply_text("Эта команда доступна только водителям.")
        return

    route = routes.get(user_id)
    if not route:
        await update.message.reply_text("У вас нет активного маршрута.")
        return

    if not route.eta:
        await update.message.reply_text("ETA еще не рассчитано.")
        return

    message = "Ожидаемое время прибытия к точкам:\n"
    for idx, loc_encrypted in enumerate(route.pickup_locations):
        loc = decrypt_data(loc_encrypted)
        eta = route.eta.get(loc)
        if eta:
            eta_time = datetime.datetime.fromisoformat(eta)
            message += f"Пассажир {route.passenger_ids[idx]}: {eta_time.strftime('%H:%M:%S')}\n"
        else:
            message += f"Пассажир {route.passenger_ids[idx]}: Не удалось рассчитать ETA\n"

    # Добавляем ETA до места назначения
    destination_eta = route.eta.get(workplace_location)
    if destination_eta:
        eta_time = datetime.datetime.fromisoformat(destination_eta)
        message += f"Пункт назначения: {eta_time.strftime('%H:%M:%S')}\n"
    else:
        message += "Пункт назначения: Не удалось рассчитать ETA\n"

    await update.message.reply_text(message)

# Команды администратора
async def admin_help(update, context):
    user_id = update.effective_user.id
    if user_id != MAIN_ADMIN_ID and user_id not in whitelist:
        await update.message.reply_text("Вы не имеете доступа к боту.")
        return

    is_authorized = context.user_data.get('is_authorized', False)
    user_role = context.user_data.get('role')
    if not is_authorized:
        await unauthorized(update, context)
        return
    if user_role != 'администратор':
        await no_permissions(update, context)
        return

    help_text = (
        "Доступные команды администратора:\n"
        "/list_routes - Показать все текущие маршруты\n"
        "/broadcast - Отправить сообщение всем пользователям\n"
        "/add_user - Добавить пользователя в белый список\n"
        "/remove_user - Удалить пользователя из белого списка\n"
        "/view_tickets - Просмотреть обращения в поддержку\n"
        "/reports - Создать отчет\n"
        "/help - Показать это сообщение"
    )
    await update.message.reply_text(help_text)

# Создание отчета
async def generate_reports(update, context):
    user_id = update.effective_user.id
    if user_id != MAIN_ADMIN_ID and user_id not in whitelist:
        await update.message.reply_text("Вы не имеете доступа к боту.")
        return

    is_authorized = context.user_data.get('is_authorized', False)
    user_role = context.user_data.get('role')
    if not is_authorized:
        await unauthorized(update, context)
        return
    if user_role != 'администратор':
        await no_permissions(update, context)
        return

    total_tickets = len(tickets)
    open_tickets = len([t for t in tickets if t['status'] != 'Закрыт'])
    closed_tickets = len([t for t in tickets if t['status'] == 'Закрыт'])

    total_routes = len(routes)
    active_routes = len([r for r in routes.values() if r.is_open])
    completed_routes = total_routes - active_routes

    report_message = (
        f"📊 **Отчет**\n\n"
        f"**Тикеты поддержки**:\n"
        f"Всего тикетов: {total_tickets}\n"
        f"Открытые тикеты: {open_tickets}\n"
        f"Закрытые тикеты: {closed_tickets}\n\n"
        f"**Маршруты**:\n"
        f"Всего маршрутов: {total_routes}\n"
        f"Активные маршруты: {active_routes}\n"
        f"Завершенные маршруты: {completed_routes}\n"
    )

    await update.message.reply_text(report_message, parse_mode=ParseMode.MARKDOWN)

# Просмотр маршрутов
async def list_routes(update, context):
    user_id = update.effective_user.id
    if user_id != MAIN_ADMIN_ID and user_id not in whitelist:
        await update.message.reply_text("Вы не имеете доступа к боту.")
        return

    is_authorized = context.user_data.get('is_authorized', False)
    user_role = context.user_data.get('role')
    if not is_authorized:
        await unauthorized(update, context)
        return
    if user_role != 'администратор':
        await no_permissions(update, context)
        return

    if routes:
        message = "Текущие маршруты:\n"
        for driver_id, route in routes.items():
            message += f"ID маршрута: {driver_id}\nВодитель: {driver_id}\nПассажиров: {len(route.passenger_ids)}\nСтатус: {'Открыт' if route.is_open else 'Завершен'}\n\n"
        message += "Введите ID маршрута для просмотра деталей или 'Назад' для возврата:"
        await update.message.reply_text(
            message,
            reply_markup=ReplyKeyboardMarkup([['Назад']], one_time_keyboard=True, resize_keyboard=True)
        )
        return VIEWING_ROUTE_DETAILS
    else:
        await update.message.reply_text("Нет активных маршрутов.")

# Просмотр деталей маршрута
async def view_route_details(update, context):
    user_input = update.message.text
    if user_input.lower() == 'назад':
        await update.message.reply_text("Возврат в главное меню.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    try:
        route_id = int(user_input)
    except ValueError:
        await update.message.reply_text("Некорректный ID маршрута. Пожалуйста, введите числовой ID или 'Назад' для возврата.")
        return VIEWING_ROUTE_DETAILS

    route = routes.get(route_id)
    if not route:
        await update.message.reply_text("Маршрут с таким ID не найден. Пожалуйста, введите корректный ID или 'Назад' для возврата.")
        return VIEWING_ROUTE_DETAILS

    route_info = (
        f"ID маршрута: {route_id}\n"
        f"Водитель: {route.driver_id}\n"
        f"Пассажиры: {', '.join(map(str, route.passenger_ids))}\n"
        f"Статус: {'Открыт' if route.is_open else 'Завершен'}\n"
    )

    reply_keyboard = [['Завершить маршрут', 'Назад']]
    await update.message.reply_text(
        route_info + "\n\nВыберите действие:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    context.user_data['current_route'] = route
    return EDITING_ROUTE

# Редактирование маршрута
async def edit_route(update, context):
    user_input = update.message.text
    route = context.user_data.get('current_route')
    if user_input.lower() == 'назад':
        return await list_routes(update, context)
    elif user_input == 'Завершить маршрут':
        route.is_open = False
        await update.message.reply_text("Маршрут завершен.", reply_markup=ReplyKeyboardRemove())
        log_action(update.effective_user.id, f"Завершил маршрут {route.driver_id}")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Некорректный выбор. Пожалуйста, выберите действие из предложенных.")
        return EDITING_ROUTE

# Обработчики тикетов поддержки
async def view_tickets(update, context):
    user_id = update.effective_user.id
    if user_id != MAIN_ADMIN_ID and user_id not in whitelist:
        await update.message.reply_text("Вы не имеете доступа к боту.")
        return

    is_authorized = context.user_data.get('is_authorized', False)
    user_role = context.user_data.get('role')
    if not is_authorized:
        await unauthorized(update, context)
        return
    if user_role != 'администратор':
        await no_permissions(update, context)
        return

    open_tickets = [ticket for ticket in tickets if ticket['status'] != 'Закрыт']
    if not open_tickets:
        await update.message.reply_text("Нет открытых тикетов.")
        return ConversationHandler.END

    ticket_list = "Открытые тикеты:\n"
    for ticket in open_tickets:
        ticket_list += f"ID: {ticket['id']}\nОт: @{ticket.get('user_name', 'NoUsername')}\nПриоритет: {ticket.get('priority', 'Не указан')}\nВремя: {ticket['timestamp']}\n\n"

    await update.message.reply_text(
        ticket_list + "Введите ID тикета для просмотра деталей или 'Назад' для возврата:",
        reply_markup=ReplyKeyboardMarkup([['Назад']], one_time_keyboard=True, resize_keyboard=True)
    )
    return VIEWING_TICKET_DETAILS

async def view_ticket_details(update, context):
    user_input = update.message.text
    if user_input.lower() == 'назад':
        await update.message.reply_text("Возврат в главное меню.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    ticket_id = user_input.strip()
    ticket = next((t for t in tickets if t['id'] == ticket_id), None)
    if not ticket:
        await update.message.reply_text("Тикет с таким ID не найден. Пожалуйста, введите корректный ID тикета или 'Назад' для возврата.")
        return VIEWING_TICKET_DETAILS

    ticket_info = (
        f"ID: {ticket['id']}\n"
        f"От: @{ticket.get('user_name', 'NoUsername')}\n"
        f"ID пользователя: {ticket['user_id']}\n"
        f"Время: {ticket['timestamp']}\n"
        f"Приоритет: {ticket['priority']}\n"
        f"Статус: {ticket['status']}\n"
        f"Сообщение: {ticket['message']}"
    )
    if ticket['admin_reply']:
        ticket_info += f"\nОтвет администратора: {ticket['admin_reply']}"

    reply_keyboard = [['Ответить', 'Изменить статус', 'Назад']]
    await update.message.reply_text(
        ticket_info + "\n\nВыберите действие:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    context.user_data['current_ticket'] = ticket
    return TICKET_ACTION

async def ticket_action(update, context):
    user_input = update.message.text
    ticket = context.user_data.get('current_ticket')
    if user_input.lower() == 'назад':
        return await view_tickets(update, context)
    elif user_input == 'Ответить':
        await update.message.reply_text(
            "Введите ваш ответ пользователю:",
            reply_markup=ReplyKeyboardMarkup([['Назад']], one_time_keyboard=True, resize_keyboard=True)
        )
        return REPLYING_TO_TICKET
    elif user_input == 'Изменить статус':
        reply_keyboard = [['Ожидает ответа', 'В работе', 'Закрыт'], ['Назад']]
        await update.message.reply_text(
            "Выберите новый статус тикета:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        return CHANGING_TICKET_STATUS
    else:
        await update.message.reply_text("Некорректный выбор. Пожалуйста, выберите действие из предложенных.")
        return TICKET_ACTION

async def changing_ticket_status(update, context):
    new_status = update.message.text
    ticket = context.user_data.get('current_ticket')
    if new_status.lower() == 'назад':
        return await view_ticket_details(update, context)
    elif new_status not in TICKET_STATUSES:
        await update.message.reply_text("Некорректный статус. Пожалуйста, выберите из предложенных вариантов.")
        return CHANGING_TICKET_STATUS
    elif new_status == 'Закрыт':
        if not ticket.get('admin_reply'):
            await update.message.reply_text(
                "Вы не можете закрыть тикет без ответа пользователю. Пожалуйста, сначала ответьте на тикет.",
                reply_markup=ReplyKeyboardMarkup([['Назад']], one_time_keyboard=True, resize_keyboard=True)
            )
            return CHANGING_TICKET_STATUS
        else:
            ticket['status'] = new_status
            save_tickets(tickets)
            await update.message.reply_text(f"Статус тикета обновлен на '{new_status}'.", reply_markup=ReplyKeyboardRemove())
            log_action(update.effective_user.id, f"Изменил статус тикета {ticket['id']} на '{new_status}'")
            return ConversationHandler.END
    else:
        # Для других статусов разрешаем изменение без ограничений
        ticket['status'] = new_status
        save_tickets(tickets)
        await update.message.reply_text(f"Статус тикета обновлен на '{new_status}'.", reply_markup=ReplyKeyboardRemove())
        log_action(update.effective_user.id, f"Изменил статус тикета {ticket['id']} на '{new_status}'")
        return ConversationHandler.END


async def reply_to_ticket(update, context):
    user_input = update.message.text
    ticket = context.user_data.get('current_ticket')
    if user_input.lower() == 'назад':
        return await view_ticket_details(update, context)
    else:
        ticket['admin_reply'] = user_input
        save_tickets(tickets)
        try:
            await context.bot.send_message(
                chat_id=ticket['user_id'],
                text=f"Ответ администратора на ваше обращение:\n{user_input}"
            )
            await update.message.reply_text(
                "Ответ отправлен пользователю. Хотите изменить статус тикета?",
                reply_markup=ReplyKeyboardMarkup([['Изменить статус', 'Назад']], one_time_keyboard=True, resize_keyboard=True)
            )
            log_action(update.effective_user.id, f"Ответил на тикет {ticket['id']}")
            return POST_REPLY_ACTION
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение пользователю {ticket['user_id']}: {e}")
            await update.message.reply_text("Не удалось отправить ответ пользователю.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END

async def post_reply_action(update, context):
    user_input = update.message.text
    if user_input == 'Изменить статус':
        reply_keyboard = [['Ожидает ответа', 'В работе', 'Закрыт'], ['Назад']]
        await update.message.reply_text(
            "Выберите новый статус тикета:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        return CHANGING_TICKET_STATUS
    elif user_input.lower() == 'назад':
        await update.message.reply_text("Возврат в главное меню.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    else:
        await update.message.reply_text("Пожалуйста, выберите действие из предложенных.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

# Команда /broadcast
async def broadcast(update, context):
    user_id = update.effective_user.id
    if user_id != MAIN_ADMIN_ID and user_id not in whitelist:
        await update.message.reply_text("Вы не имеете доступа к боту.")
        return ConversationHandler.END

    is_authorized = context.user_data.get('is_authorized', False)
    user_role = context.user_data.get('role')
    if not is_authorized:
        await unauthorized(update, context)
        return ConversationHandler.END
    if user_role != 'администратор':
        await no_permissions(update, context)
        return ConversationHandler.END

    await update.message.reply_text(
        "Пожалуйста, введите сообщение для рассылки всем пользователям:",
        reply_markup=ReplyKeyboardMarkup([['Назад']], one_time_keyboard=True, resize_keyboard=True)
    )
    return BROADCASTING

async def handle_broadcast_message(update, context):
    user_input = update.message.text
    if user_input.lower() == 'назад':
        await update.message.reply_text("Рассылка отменена.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    message_to_send = user_input
    user_ids = whitelist.copy()
    user_ids.add(MAIN_ADMIN_ID)
    if user_ids:
        for user_id in user_ids:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=message_to_send
                )
            except Exception as e:
                logger.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
        await update.message.reply_text("Сообщение отправлено всем пользователям.", reply_markup=ReplyKeyboardRemove())
        log_action(update.effective_user.id, "Выполнил рассылку")
    else:
        await update.message.reply_text("Нет пользователей для отправки сообщения.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# Добавление и удаление пользователей из белого списка
async def add_user(update, context):
    user_id = update.effective_user.id
    if user_id != MAIN_ADMIN_ID and user_id not in whitelist:
        await update.message.reply_text("Вы не имеете доступа к боту.")
        return ConversationHandler.END

    is_authorized = context.user_data.get('is_authorized', False)
    user_role = context.user_data.get('role')
    if not is_authorized:
        await unauthorized(update, context)
        return ConversationHandler.END
    if user_role != 'администратор':
        await no_permissions(update, context)
        return ConversationHandler.END

    await update.message.reply_text(
        "Введите ID пользователя, которого вы хотите добавить в белый список:",
        reply_markup=ReplyKeyboardMarkup([['Назад']], one_time_keyboard=True, resize_keyboard=True)
    )
    return ADDING_USER

async def handle_add_user(update, context):
    user_input = update.message.text
    if user_input.lower() == 'назад':
        await update.message.reply_text("Добавление пользователя отменено.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    try:
        user_id = int(user_input)
        whitelist.add(user_id)
        save_whitelist(whitelist)
        await update.message.reply_text(f"Пользователь {user_id} добавлен в белый список.", reply_markup=ReplyKeyboardRemove())
        log_action(update.effective_user.id, f"Добавил пользователя {user_id} в белый список")
    except ValueError:
        await update.message.reply_text("Некорректный ID пользователя. Пожалуйста, введите числовой ID или 'Назад' для возврата.")
        return ADDING_USER
    return ConversationHandler.END

# Удаление пользователя из белого списка
async def remove_user(update, context):
    user_id = update.effective_user.id
    if user_id != MAIN_ADMIN_ID and user_id not in whitelist:
        await update.message.reply_text("Вы не имеете доступа к боту.")
        return ConversationHandler.END

    is_authorized = context.user_data.get('is_authorized', False)
    user_role = context.user_data.get('role')
    if not is_authorized:
        await unauthorized(update, context)
        return ConversationHandler.END
    if user_role != 'администратор':
        await no_permissions(update, context)
        return ConversationHandler.END

    await update.message.reply_text(
        "Введите ID пользователя, которого вы хотите удалить из белого списка:",
        reply_markup=ReplyKeyboardMarkup([['Назад']], one_time_keyboard=True, resize_keyboard=True)
    )
    return REMOVING_USER

async def handle_remove_user(update, context):
    user_input = update.message.text
    if user_input.lower() == 'назад':
        await update.message.reply_text("Удаление пользователя отменено.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    try:
        user_id = int(user_input)
        if user_id == MAIN_ADMIN_ID:
            await update.message.reply_text("Невозможно удалить главного администратора из белого списка.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        whitelist.discard(user_id)
        save_whitelist(whitelist)
        await update.message.reply_text(f"Пользователь {user_id} удален из белого списка.", reply_markup=ReplyKeyboardRemove())
        log_action(update.effective_user.id, f"Удалил пользователя {user_id} из белого списка")
    except ValueError:
        await update.message.reply_text("Некорректный ID пользователя. Пожалуйста, введите числовой ID или 'Назад' для возврата.")
        return REMOVING_USER
    return ConversationHandler.END

# Главная функция
def main():
    application = Application.builder().token(telegram_bot_token).build()

    # Обработчик
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("login", login),
            CommandHandler('broadcast', broadcast),
            CommandHandler('add_user', add_user),
            CommandHandler('remove_user', remove_user),
            CommandHandler('help', admin_help),
            CommandHandler('list_routes', list_routes),
            CommandHandler("finish", finish_route),
            CommandHandler("view_tickets", view_tickets),
            CommandHandler('reports', generate_reports),
        ],
        states={
            CHOOSING_ROLE: [
                CallbackQueryHandler(choose_role_login, pattern='^role_.*$'),
                CallbackQueryHandler(back_to_login, pattern='^back_to_login$'),
            ],
            ENTERING_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_password)],
            CONTACTING_SUPPORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, contacting_support)],
            SELECTING_PRIORITY: [CallbackQueryHandler(selecting_priority, pattern='^priority_.*$')],
            WAITING_FOR_LOCATION: [MessageHandler((filters.LOCATION | filters.TEXT) & ~filters.COMMAND, waiting_for_location)],
            BROADCASTING: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_message)],
            ADDING_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_user)],
            REMOVING_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_remove_user)],
            VIEWING_TICKET_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, view_ticket_details)],
            TICKET_ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_action)],
            REPLYING_TO_TICKET: [MessageHandler(filters.TEXT & ~filters.COMMAND, reply_to_ticket)],
            POST_REPLY_ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_reply_action)],
            CHANGING_TICKET_STATUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, changing_ticket_status)],
            VIEWING_ROUTE_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, view_route_details)],
            EDITING_ROUTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_route)],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("login", login),
            MessageHandler(filters.Regex('^(Отмена|Назад)$'), lambda update, context: update.message.reply_text("Действие отменено.", reply_markup=ReplyKeyboardRemove()))
        ],
    )

    # Регистрация обработчиков
    application.add_handler(conv_handler)

    # Обработчик для сообщений с живым местоположением
    application.add_handler(MessageHandler(filters.LOCATION & filters.ChatType.PRIVATE, handle_live_location))

    # Команда /show_eta для водителя
    application.add_handler(CommandHandler('show_eta', show_eta))

    # Обработчик для всех остальных сообщений
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, unauthorized))

    application.run_polling()

if __name__ == "__main__":
    main()
