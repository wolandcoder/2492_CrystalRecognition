"""Интерактивный клиент для удалённого управления Mach3 (генерирует G-код на клиенте)."""

import time
from mach3_common import MyData, generate_gcode, send_command_string

DEFAULT_IP = '192.168.1.125'
DEFAULT_PORT = 5555


def interactive_mode():
    print(f"=== Интерактивный клиент MyData ===")
    print(f"Сервер по умолчанию: {DEFAULT_IP}:{DEFAULT_PORT}")
    print("Команды:")
    print("  set ip <адрес>        - задать IP сервера")
    print("  set port <порт>       - задать порт сервера")
    print("  set x <число>         - задать X")
    print("  set y <число>         - задать Y")
    print("  set z <число>         - задать Z")
    print("  set a <число>         - задать A")
    print("  set f <число>         - задать скорость подачи f")
    print("  set cmd <текст>       - задать готовую G-код команду")
    print("  show                  - показать текущий объект")
    print("  clear motion          - сбросить параметры движения")
    print("  gcode                 - показать сгенерированную строку G-кода")
    print("  send                  - отправить команду на сервер")
    print("  get                   - запросить координаты со станка")
    print("  move x/y/z/a <число>  - перемещение по оси на указанное значение")
<<<<<<< HEAD
    print("  send_command (tozero, fullcollibration, allzero) - выполнить последовательность")
=======
    print("  set_command (tozero, fullcollibration, allzero) - выполнить последовательность")
>>>>>>> hse/main
    print("  quit                  - выход")
    print("-" * 60)

    data = MyData(ip=DEFAULT_IP, port=DEFAULT_PORT)

    while True:
        cmd_line = input("> ").strip()
        if not cmd_line:
            continue

        parts = cmd_line.split(maxsplit=2)
        action = parts[0].lower()

        if action == "quit":
            # Отправляем QUIT
            quit_obj = MyData(ip=data.connection.ip, port=data.connection.port)
            quit_obj.motion.set_command("QUIT")
            send_command_string(quit_obj, expect_response=False)
            break

        elif action == "show":
            print(f"Текущий объект:\n  {data.connection}\n  {data.motion}")

        elif action == "clear":
            if len(parts) > 1 and parts[1].lower() == "motion":
                data.motion.clear()
                print("Параметры движения сброшены.")
            else:
                print("Используйте 'clear motion' для сброса движения.")

        elif action == "gcode":
            gcode = generate_gcode(data)
            print(f"Сгенерированный G-код: '{gcode}'")

        elif action == "send":
            if data.motion.command is None and all(v is None for v in [data.motion.X, data.motion.Y, data.motion.Z, data.motion.A]):
                print("Предупреждение: не заданы ни координаты, ни команда. Отправлять нечего.")
                continue
            print(f"Отправляю команду...")
            response = send_command_string(data)
            print(f"Ответ: {response}")
            # После отправки сбрасываем команду и ручной режим (координаты и подача остаются)
            data.motion.set_command(None).set_manual(None)

        elif action == "get":
<<<<<<< HEAD
            data.motion.get_DRO()
=======
            data.motion.get_DRO(data.connection)
>>>>>>> hse/main

        elif action == "move":
            if len(parts) < 3:
                print("Использование: move <x/y/z/a> <значение>")
                continue
            axis = parts[1].lower()
            try:
                delta = float(parts[2])
<<<<<<< HEAD
                data.motion.move_on(axis, delta)  
            except ValueError:
                print("Ошибка: значение должно быть числом")

        elif action == "send_command":
            if len(parts) < 2:
                print("Использование: send_command <tozero/fullcollibration/allzero>")
=======
                data.motion.move_on(axis, delta, data.connection)
            except ValueError:
                print("Ошибка: значение должно быть числом")

        elif action == "set_command":
            if len(parts) < 2:
                print("Использование: set_command <tozero/fullcollibration/allzero>")
>>>>>>> hse/main
                continue
            command = parts[1].lower()
            data.motion.send_command(command)

        elif action == "set":
            if len(parts) < 2:
                print("Использование: set <поле> <значение>")
                continue
            field = parts[1].lower()
            value = parts[2] if len(parts) > 2 else None

            if field == "ip":
                if value is not None:
                    data.connection.set_ip(value)
                print(f"IP = {data.connection.ip}")
            elif field == "port":
                if value is not None:
                    try:
                        data.connection.set_port(int(value))
                    except ValueError:
                        print("Ошибка: порт должен быть целым числом.")
                print(f"PORT = {data.connection.port}")
            elif field == "x":
                if value is not None:
                    try:
                        data.motion.set_x(float(value))
                    except ValueError:
                        print("Ошибка: введите число.")
                else:
                    data.motion.set_x(None)
                print(f"X = {data.motion.X}")
            elif field == "y":
                if value is not None:
                    try:
                        data.motion.set_y(float(value))
                    except ValueError:
                        print("Ошибка: введите число.")
                else:
                    data.motion.set_y(None)
                print(f"Y = {data.motion.Y}")
            elif field == "z":
                if value is not None:
                    try:
                        data.motion.set_z(float(value))
                    except ValueError:
                        print("Ошибка: введите число.")
                else:
                    data.motion.set_z(None)
                print(f"Z = {data.motion.Z}")
            elif field == "a":
                if value is not None:
                    try:
                        data.motion.set_a(float(value))
                    except ValueError:
                        print("Ошибка: введите число.")
                else:
                    data.motion.set_a(None)
                print(f"A = {data.motion.A}")
            elif field == "f":
                if value is not None:
                    try:
                        data.motion.set_feed(int(value))
                    except ValueError:
                        print("Ошибка: введите целое число.")
                else:
                    data.motion.set_feed(None)
                print(f"f = {data.motion.f}")
            elif field in ("cmd", "command"):
                data.motion.set_command(value)
                print(f"command = '{data.motion.command}'")
            else:
                print(f"Неизвестное поле: {field}")
                print("Доступные поля: ip, port, x, y, z, a, f, cmd")
        else:
            print("Неизвестная команда. Введите 'show' для списка команд.")


if __name__ == "__main__":
    interactive_mode()