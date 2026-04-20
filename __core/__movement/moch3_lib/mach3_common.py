"""Общие компоненты: класс MyData, функция генерации G-кода и сетевые утилиты."""

import socket
import pickle
import struct
import time

class ColbaConfig:
    """Конфигурация колбы: координаты центра, радиус, высота."""
    def __init__(self, x=None, y=None, r=None, h=None):
        self.x = float(x) if x is not None else None
        self.y = float(y) if y is not None else None
        self.r = float(r) if r is not None else None
        self.h = float(h) if h is not None else None

    def __repr__(self):
        return f"ColbaConfig(x={self.x}, y={self.y}, r={self.r}, h={self.h})"


class MyData:
    """
    Основной контейнер данных для обмена с сервером Mach3.
    Содержит параметры движения и настройки подключения.
    """

    class Connection:
        """Параметры сетевого подключения."""
        def __init__(self, ip=None, port=None):
            self._ip = ip
            self._port = port

        @property
        def ip(self): 
            return self._ip
        
        @property
        def port(self): 
            return self._port

        def set_ip(self, ip): 
            self._ip = str(ip)
            return self
        
        def set_port(self, port): 
            self._port = int(port)
            return self

        def __repr__(self):
            return f"Connection(ip='{self._ip}', port={self._port})"

    class Motion:
        """Параметры движения и команды для станка."""
        
        # Значения колбы по умолчанию
        DEFAULT_COLBA = ColbaConfig(
            x=0,    # центр колбы по X
            y=0,    # центр колбы по Y
            r=45,   # радиус колбы
            h=10    # высота колбы
        )
        
        def __init__(self, colba: ColbaConfig = None, parent=None):
            self._X = None
            self._Y = None
            self._Z = None
            self._A = None
            self._f = None
            self._command = None
            self._manual_command = None
            self._colba_information = colba if colba is not None else self.DEFAULT_COLBA
            self._parent = parent  # ссылка на родительский объект MyData
        
        @property
        def connection(self):
            """Получаем connection от родительского объекта"""
            if self._parent:
                return self._parent.connection
            return None

        # --- Свойства только для чтения ---
        @property
        def X(self): 
            return self._X
        
        @property
        def Y(self): 
            return self._Y
        
        @property
        def Z(self): 
            return self._Z
        
        @property
        def A(self): 
            return self._A
        
        @property
        def f(self): 
            return self._f
        
        @property
        def command(self): 
            return self._command
        
        @property
        def manual_command(self): 
            return self._manual_command
        
        @property
        def colba_information(self): 
            return self._colba_information

        # --- Методы установки координат и подачи ---
        def set_x(self, value): 
            if value is not None:
                val = float(value)
                if self._parent and self._parent.connection:
                    self._check_circle_constraint(val, "X", self._parent.connection)
                self._X = val
            else:
                self._X = None
            return self

        def set_y(self, value): 
            if value is not None:
                val = float(value)
                if self._parent and self._parent.connection:
                    self._check_circle_constraint(val, "Y", self._parent.connection)
                self._Y = val
            else:
                self._Y = None
            return self

        def set_z(self, value): 
            if value is not None:
                val = float(value)
                if not (0 <= val <= 37):
                    raise ValueError(f"Z must be between 0 and 37 (inclusive), got {val}")
                self._Z = val
            else:
                self._Z = None
            return self

        def set_a(self, value): 
            self._A = float(value) if value is not None else None
            return self

        def set_feed(self, f): 
            if f is not None:
                val = int(f)
                if not (val > 0):
                    raise ValueError(f"Feed must be greater than 0, got {val}")
                self._f = val
            else:
                self._f = None
            return self

        def set_command(self, cmd): 
            self._command = str(cmd) if cmd is not None else None
            return self

        def set_manual(self, manual): 
            self._manual_command = str(manual) if manual is not None else None
            return self

        # --- Проверка ограничения круга ---
        def _check_circle_constraint(self, val, axis, connection):
            """Проверяет, что если Z < h, то точка (X, Y) находится внутри круга."""
            if not connection:
                return
                
            # Создаем временный объект для получения текущих координат
            temp_data = MyData(ip=connection.ip, port=connection.port)
            temp_data.motion.get_DRO(connection)
            
            current_z = temp_data.motion.Z
            colba = self._colba_information
            
            # Проверка границ
            # Если Z >= h, проверка круга не требуется
            if current_z >= colba.h:
                if axis == "X":
                    if not (0 <= val <= 150):
                        raise ValueError(f"X must be between 0 and 150, got {val}")
                else:  # axis == "Y"
                    if not (0 <= val <= 250):
                        raise ValueError(f"Y must be between 0 and 250, got {val}")
            
                return
            
            # Проверяем, что точка (X, Y) внутри круга
            test_x = val if axis == "X" else (self._X or 0)
            test_y = val if axis == "Y" else (self._Y or 0)
            
            dx = test_x - colba.x
            dy = test_y - colba.y
            
            distance_squared = dx * dx + dy * dy
            radius_squared = colba.r * colba.r
            
            print(f"Проверка круга: точка ({test_x}, {test_y}), центр ({colba.x}, {colba.y})")
            print(f"Квадрат расстояния: {distance_squared}, квадрат радиуса: {radius_squared}")
            
            if distance_squared > radius_squared:
                raise ValueError(
                    f"Точка ({test_x}, {test_y}) находится вне круга с центром "
                    f"({colba.x}, {colba.y}) и радиусом {colba.r} при Z={current_z} < h={colba.h}"
                )
        
        def send_command(self, command):
            if not self.connection:
                print("Ошибка: нет подключения")
                return
                
            ip, port = self.connection.ip, self.connection.port
            
            if command == "tozero":
                print("Выполнение TOZERO...")

                data1 = MyData(ip=ip, port=port)
                data1.motion.set_command("x1 y1 z1 f700")
                resp1 = send_command_string(data1)
                print(f"  Шаг 1: {resp1}")
                time.sleep(1)

                data2 = MyData(ip=ip, port=port)
                data2.motion.set_command("x-1 y-1 z-1 f70")
                resp2 = send_command_string(data2)
                print(f"  Шаг 2: {resp2}")
                time.sleep(1)

                data3 = MyData(ip=ip, port=port)
                data3.motion.set_command("G92 X0 Y0 Z0 A0")
                resp3 = send_command_string(data3)
                print(f"  Шаг 3: {resp3}")

                get_obj = MyData(ip=ip, port=port)
                get_obj.motion.set_command("GET_DRO")
                coords = send_command_string(get_obj)
                print(f"Координаты после обнуления: {coords}")
                
            elif command == "fullcollibration":
                data1 = MyData(ip=ip, port=port)
                data1.motion.set_command("x-160 y-250 z-100 f700")
                resp1 = send_command_string(data1)
                print(f"  Шаг 1: {resp1}")
                time.sleep(1)

                data2 = MyData(ip=ip, port=port)
                data2.motion.set_command("x12 y49")
                resp2 = send_command_string(data2)
                print(f"  Шаг 2: {resp2}")
                time.sleep(1)

                data3 = MyData(ip=ip, port=port)
                data3.motion.set_command("z-39 f200")
                resp3 = send_command_string(data3)
                print(f"  Шаг 3: {resp3}")
                time.sleep(1)

                data4 = MyData(ip=ip, port=port)
                data4.motion.set_command("G92 X0 Y0 Z0 A0")
                resp4 = send_command_string(data4)
                print(f"  Шаг 4: {resp4}")
                time.sleep(1)
                
            elif command == "allzero":
                data1 = MyData(ip=ip, port=port)
                data1.motion.set_command("G92 X0 Y0 Z0 A0")
                resp1 = send_command_string(data1)
                print(f"  Ответ: {resp1}")
                time.sleep(1)

            elif command == "zerotocamera":
                data1 = MyData(ip=ip, port=port)
                data1.motion.set_command("X44 Y6")
                resp1 = send_command_string(data1)
                print(f"  Ответ: {resp1}")
                time.sleep(1)
                data2 = MyData(ip=ip, port=port)
                data2.motion.set_command("G92 X0 Y0 Z0 A0")
                resp2 = send_command_string(data2)
                print(f"  Ответ: {resp2}")
                time.sleep(1)
            elif command == "zerotozond":
                data1 = MyData(ip=ip, port=port)
                data1.motion.set_command("X-44 Y-6")
                resp1 = send_command_string(data1)
                print(f"  Ответ: {resp1}")
                time.sleep(1)
                data2 = MyData(ip=ip, port=port)
                data2.motion.set_command("G92 X0 Y0 Z0 A0")
                resp2 = send_command_string(data2)
                print(f"  Ответ: {resp2}")
                time.sleep(1)

        def move_on(self, axis, delta, connection):
            """Перемещение по оси на указанное значение"""
            ip, port = connection.ip, connection.port
            
            # Получаем текущие координаты
            data1 = MyData(ip=ip, port=port)
            data1.motion.get_DRO(connection)
            print(f"Текущие координаты: X={data1.motion.X}, Y={data1.motion.Y}, Z={data1.motion.Z}, A={data1.motion.A}")
            
            # Создаем команду на перемещение
            data2 = MyData(ip=ip, port=port)
            if axis == "x":
                new_x = (data1.motion.X or 0) + delta
                data2.motion.set_x(new_x)
                print(f"Перемещение по X на {delta}, новая X={new_x}")
            elif axis == "y":
                new_y = (data1.motion.Y or 0) + delta
                data2.motion.set_y(new_y)
                print(f"Перемещение по Y на {delta}, новая Y={new_y}")
            elif axis == "z":
                new_z = (data1.motion.Z or 0) + delta
                data2.motion.set_z(new_z)
                print(f"Перемещение по Z на {delta}, новая Z={new_z}")
            elif axis == "a":
                new_a = (data1.motion.A or 0) + delta
                data2.motion.set_a(new_a)
                print(f"Перемещение по A на {delta}, новая A={new_a}")
            else:
                print(f"Ошибка: неизвестная ось '{axis}'")
                return
            
            resp2 = send_command_string(data2)
            print(f"Ответ сервера: {resp2}")
        
        def get_DRO(self, connection):
            """Получает текущие координаты со станка"""
            if not connection:
                print("Ошибка: нет подключения")
                return None
                
            get_obj = MyData(ip=connection.ip, port=connection.port)
            get_obj.motion.set_command("GET_DRO")
            response = send_command_string(get_obj)
            print(f"Ответ сервера: {response}")
            
            # Парсим ответ и устанавливаем координаты в текущий объект
            if response and not response.startswith("ОШИБКА") and response != "NO_DATA":
                try:
                    # Очищаем строку от лишних пробелов и символов перевода строки
                    response_clean = response.strip()
                    
                    # Предполагаем формат: "X36.895 Y25.95 Z-0.423 A-0.844" или "36.895,25.95,-0.423,-0.844"
                    if ',' in response_clean:
                        # Формат с запятыми
                        parts = response_clean.split(',')
                        if len(parts) >= 4:
                            self._X = float(parts[0].strip())
                            self._Y = float(parts[1].strip())
                            self._Z = float(parts[2].strip())
                            self._A = float(parts[3].strip())
                        else:
                            print(f"Недостаточно значений: ожидалось 4, получено {len(parts)}")
                            return None
                    else:
                        # Формат с пробелами X... Y... Z... A...
                        parts = response_clean.split()
                        for part in parts:
                            if len(part) > 1 and part[0] in 'XYZAP':
                                axis = part[0]
                                value = float(part[1:])
                                if axis == 'X':
                                    self._X = value
                                elif axis == 'Y':
                                    self._Y = value
                                elif axis == 'Z':
                                    self._Z = value
                                elif axis == 'A':
                                    self._A = value
                    
                    print(f"Координаты обновлены: X={self._X}, Y={self._Y}, Z={self._Z}, A={self._A}")
                    return response
                    
                except (ValueError, IndexError) as e:
                    print(f"Не удалось распарсить координаты: {e}")
                    print(f"Проблемная строка: '{response}'")
                    return None
            else:
                print("Не удалось получить координаты со станка")
                return None
    
        def clear(self):
            self._X = self._Y = self._Z = self._A = None
            self._f = None
            self._command = None
            self._manual_command = None
            return self

        def __repr__(self):
            return (f"Motion(X={self._X}, Y={self._Y}, Z={self._Z}, A={self._A}, "
                    f"f={self._f}, command='{self._command}', manual='{self._manual_command}', "
                    f"colba={self._colba_information})")

    def __init__(self, ip=None, port=None, colba: ColbaConfig = None):
        self.connection = self.Connection(ip, port)
        self.motion = self.Motion(colba, parent=self)

    def __repr__(self):
        return f"MyData(motion={self.motion}, connection={self.connection})"


def generate_gcode(data: MyData) -> str:
    """
    Генерирует строку G-кода из объекта MyData.
    Приоритет:
    1. Если задано data.motion.command - возвращает его как есть
    2. Иначе собирает G-код из координат X, Y, Z, A и скорости f
    """
    # Если явно задана команда - просто возвращаем её
    if data.motion.command is not None and data.motion.command.strip():
        return data.motion.command.strip()
    
    # Иначе собираем G-код из координат
    parts = []
    has_coords = any(v is not None for v in [data.motion.X, data.motion.Y, data.motion.Z, data.motion.A])
    
    if has_coords:
        if data.motion.f is not None:
            parts.append("G1")
        else:
            parts.append("G0")
    
    if data.motion.X is not None:
        parts.append(f"X{data.motion.X:.3f}")
    if data.motion.Y is not None:
        parts.append(f"Y{data.motion.Y:.3f}")
    if data.motion.Z is not None:
        parts.append(f"Z{data.motion.Z:.3f}")
    if data.motion.A is not None:
        parts.append(f"A{data.motion.A:.3f}")
    if data.motion.f is not None:
        parts.append(f"F{data.motion.f}")
    
    return " ".join(parts) if parts else ""


# --- Сетевые функции (отправка строки) ---
def send_object(sock, obj):
    """Отправить pickle-объект через сокет с префиксом длины."""
    data = pickle.dumps(obj)
    sock.send(struct.pack('>I', len(data)))
    sock.send(data)


def recv_exact(sock, n):
    """Принять ровно n байт из сокета."""
    data = b''
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data += packet
    return data


def receive_object(sock):
    """Принять pickle-объект из сокета."""
    raw_len = recv_exact(sock, 4)
    if not raw_len:
        return None
    data_len = struct.unpack('>I', raw_len)[0]
    data = recv_exact(sock, data_len)
    if not data:
        return None
    return pickle.loads(data)


def send_command_string(data_obj: MyData, expect_response=True) -> str:
    """
    Генерирует строку G-кода из MyData, отправляет её на сервер.
    Возвращает ответ сервера (координаты или сообщение).
    """
    ip = data_obj.connection.ip
    port = data_obj.connection.port
    if ip is None or port is None:
        return "ОШИБКА: не заданы IP или порт"

    gcode_str = generate_gcode(data_obj)
    if not gcode_str:
        return "ОШИБКА: не удалось сгенерировать команду"

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((ip, port))
        send_object(sock, gcode_str)   # отправляем строку
        if expect_response:
            response = receive_object(sock)
            sock.close()
            return response
        sock.close()
        return None
    except Exception as e:
        return f"ОШИБКА: {e}"