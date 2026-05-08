import time
from mach3_common import MyData, Connection, ColbaConfig

DEFAULT_IP = '192.168.1.125'
DEFAULT_PORT = 5555

# создание обекта где будут хранится все данные (тут используется конструктор сразу с ip и портом, можно без него)
data = MyData(ip=DEFAULT_IP, port=DEFAULT_PORT)

# вот так изменяется
data.motion.set_connection(Connection('192.168.1.100', 5556))



# ==================== тут инфа про рабочую зону
# можно сделать обект колба и просто передаьт его в наш обект хранения координат
colba = ColbaConfig(x=0.0, y=0.0, r=40.0, h=10.0)
data.motion.colba_information = colba

# или просто использовать сеттер, можно стетать по 1 координате
data.motion.set_colba_params(x=0.0, y=0.0, r=40.0, h=10.0)
data.motion.set_colba_params(x=1)




# ==================== тут движение
data.motion.get_DRO() # запрос координат со стонка + сохранение этих координат в data.motion
# тут показано как эти координаты можно отдельно запросить, но они запрашиваются НЕ со стонка, с те, что сохранены в data.motion
print(f"Координаты X={data.motion.X}, Y={data.motion.Y}, Z={data.motion.Z}, A={data.motion.A}") 

data.motion.move_on("y", -1.0)   # двигает в относительных координатах, автоматически учитывает ограничения колбы
data.motion.move_on("x", 1.0)
data.motion.move_on("a", 360.0)
# х, y - в мм, координаты а в градусах
#не помню конкретную точность, но 2-3 знака после запятой должно быть




# ==================== тут инфа про работу с классом кристаллов
# Получение параметров сетки
rows = data.grid.rows           # количество строк
cols = data.grid.cols           # количество столбцов
row_spacing = data.grid.row_spacing    # расстояние между строками
col_spacing = data.grid.col_spacing    # расстояние между столбцами

# Текущая позиция
current_row = data.grid.current_row    # текущий индекс строки (0-based)
current_col = data.grid.current_col    # текущий индекс колонки (0-based)
finished = data.grid.finished       # True если сетка полностью пройдена



# Изменение размеров сетки (с проверкой границ)
data.grid.rows = 10      # новое количество строк
data.grid.cols = 8       # новое количество столбцов

# Изменение расстояний
data.grid.row_spacing = 15.5   # расстояние между строками
data.grid.col_spacing = 12.0   # расстояние между колонками

# Установка текущей позиции
data.grid.current_row = 3      # перейти к строке 3
data.grid.current_col = 2      # перейти к колонке 2

# переместиться к следующей позиции
data.grid.next()  # true если удалось, иначе false

# Сброс в указанную позицию (или в начало)
data.grid.reset()              # сброс на (0, 0)









# ЕЩЕ КОМАНДЫ, НО НЕ ЮЗАЙ ИХ, НА ЭТОМ ЭТАПЕ, НАМ НУЖНО СНАЧАЛА ДОГОВОРИТЬСЯ ГДЕ И КАК ОНИ БУДУТ ИСПОЛЬЗОВАНЫ

# Выполняем предопределённую команду (например, перемещение в зону)
data.motion.send_command("zerotozond")
# Перемещение по оси Y на -1 мм
# Устанавливаем точное значение X и отправляем без ожидания ответа
data.motion.set_x(40.0)
data.motion.set_command(None)    # чтобы сгенерировать G-код из координат
data.motion.send(expect_response=False)
# Снова обновляем координаты
data.motion.get_DRO()