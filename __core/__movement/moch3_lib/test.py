import time
import socket
from mach3_common import MyData, generate_gcode, send_command_string, send_object

DEFAULT_IP = '192.168.1.125'
DEFAULT_PORT = 5555


data = MyData(ip=DEFAULT_IP, port=DEFAULT_PORT)
data.motion.get_DRO(data.connection) #Обновляет все координаты

# data.motion.send_command("zerotozond")

data.motion.move_on("y",-1,data.connection)

# data.motion.set_x(40)
# send_command_string(data, False) #false - без ответа

data.motion.get_DRO(data.connection) #обновить все координаты
x = data.motion._X
