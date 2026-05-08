# mach3_simulator.py (исправленная версия)
import socket
import pickle
import struct
import os
import threading
import time
import re

HOST = '0.0.0.0'
PORT = 5555
COMMAND_FILE = r"C:\python\zavod\data\Mach3Command.txt"
DRO_FILE = r"C:\python\zavod\data\DROData.txt"

os.makedirs(os.path.dirname(COMMAND_FILE), exist_ok=True)

current_pos = [0.0, 0.0, 0.0, 0.0]
pos_lock = threading.Lock()
file_lock = threading.Lock()

def send_object(sock, obj):
    data = pickle.dumps(obj)
    sock.send(struct.pack('>I', len(data)))
    sock.send(data)

def recv_exact(sock, n):
    data = b''
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data += packet
    return data

def receive_object(sock):
    raw_len = recv_exact(sock, 4)
    if not raw_len:
        return None
    data_len = struct.unpack('>I', raw_len)[0]
    data = recv_exact(sock, data_len)
    if not data:
        return None
    return pickle.loads(data)

def write_dro(x, y, z, a):
    with file_lock:
        with open(DRO_FILE, 'w') as f:
            f.write(f"{x:.3f},{y:.3f},{z:.3f},{a:.3f}")

def read_dro():
    with file_lock:
        if not os.path.exists(DRO_FILE):
            return None
        with open(DRO_FILE, 'r') as f:
            line = f.readline().strip()
            if not line:
                return None
            parts = line.split(',')
            if len(parts) >= 4:
                return tuple(float(p) for p in parts[:4])
    return None

def parse_movement_command(cmd):
    targets = {}
    cmd_upper = cmd.upper().split('(')[0].strip()
    matches = re.findall(r'([XYZA])([-+]?\d*\.?\d+)', cmd_upper)
    for axis, val_str in matches:
        try:
            targets[axis] = float(val_str)
        except ValueError:
            pass
    return targets

def is_movement_command(cmd):
    if not cmd:
        return False
    cmd_upper = cmd.strip().upper().split('(')[0].strip()
    if any(cmd_upper.startswith(code) for code in ('G0', 'G1', 'G2', 'G3')):
        return True
    if any(axis in cmd_upper for axis in ('X', 'Y', 'Z', 'A')):
        return True
    return False

def process_movement(cmd):
    targets = parse_movement_command(cmd)
    with pos_lock:
        if 'X' in targets:
            current_pos[0] = targets['X']
        if 'Y' in targets:
            current_pos[1] = targets['Y']
        if 'Z' in targets:
            current_pos[2] = targets['Z']
        if 'A' in targets:
            current_pos[3] = targets['A']
    write_dro(*current_pos)
    time.sleep(0.5)                 # имитация времени движения
    return read_dro()

def handle_client(conn, addr):
    print(f"[+] Клиент: {addr}")
    with conn:
        while True:
            obj = receive_object(conn)
            if obj is None:
                break
            cmd = obj.strip() if isinstance(obj, str) else str(obj)
            print(f"[{addr}] Команда: {cmd}")

            if cmd.upper() == "QUIT":
                send_object(conn, "BYE")
                break
            elif cmd.upper() == "GET_DRO":
                coords = read_dro()
                if coords:
                    resp = f"{coords[0]:.3f},{coords[1]:.3f},{coords[2]:.3f},{coords[3]:.3f}"
                else:
                    resp = "NO_DATA"
                send_object(conn, resp)
            else:
                # Сохраняем команду в файл (как в оригинале)
                with file_lock:
                    try:
                        if os.path.exists(COMMAND_FILE):
                            os.remove(COMMAND_FILE)
                        with open(COMMAND_FILE, 'w') as f:
                            f.write(cmd)
                    except Exception as e:
                        print(f"[Ошибка записи команды] {e}")

                if is_movement_command(cmd):
                    # Одно сообщение с финальными координатами (без MOVING_STARTED)
                    final_coords = process_movement(cmd)
                    if final_coords:
                        resp = f"{final_coords[0]:.3f},{final_coords[1]:.3f},{final_coords[2]:.3f},{final_coords[3]:.3f}"
                    else:
                        resp = "TIMEOUT"
                    send_object(conn, resp)
                else:
                    time.sleep(0.3)
                    coords = read_dro()
                    if coords:
                        resp = f"{coords[0]:.3f},{coords[1]:.3f},{coords[2]:.3f},{coords[3]:.3f}"
                    else:
                        resp = "OK"
                    send_object(conn, resp)
    print(f"[-] Клиент отключён: {addr}")

def start_server():
    write_dro(*current_pos)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, PORT))
        s.listen()
        print(f"[Симулятор Mach3] {HOST}:{PORT}")
        try:
            while True:
                conn, addr = s.accept()
                threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
        except KeyboardInterrupt:
            print("\n[Сервер остановлен]")

if __name__ == "__main__":
    start_server()