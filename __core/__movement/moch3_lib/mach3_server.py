# mach3_server.py (запускается на компьютере с Mach3)
import socket
import pickle
import struct
import os
import threading
import time

HOST = '0.0.0.0'
PORT = 5555
COMMAND_FILE = r"C:\python\zavod\data\Mach3Command.txt"
DRO_FILE = r"C:\python\zavod\data\DROData.txt"

os.makedirs(os.path.dirname(COMMAND_FILE), exist_ok=True)

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

def write_command(cmd):
    try:
        if os.path.exists(COMMAND_FILE):
            os.remove(COMMAND_FILE)
        with open(COMMAND_FILE, 'w') as f:
            f.write(cmd)
        return True
    except Exception as e:
        print(f"[Ошибка записи] {e}")
        return False

def read_dro():
    try:
        if not os.path.exists(DRO_FILE):
            return None
        with open(DRO_FILE, 'r') as f:
            line = f.readline().strip()
            if not line:
                return None
            parts = line.split(',')
            if len(parts) >= 4:
                return tuple(float(p) for p in parts[:4])
    except Exception:
        pass
    return None

def wait_for_movement_end(max_wait=60, stable_time=0.5, threshold=0.01):
    start = time.time()
    last_coords = None
    stable_start = None
    while time.time() - start < max_wait:
        coords = read_dro()
        if coords is None:
            time.sleep(0.2)
            continue
        if last_coords is None:
            last_coords = coords
            continue
        changed = any(abs(coords[i] - last_coords[i]) > threshold for i in range(4))
        if changed:
            last_coords = coords
            stable_start = None
        else:
            if stable_start is None:
                stable_start = time.time()
            elif time.time() - stable_start >= stable_time:
                return coords
        time.sleep(0.15)
    return read_dro()

def is_movement_command(cmd):
    if not cmd:
        return False
    cmd_upper = cmd.upper().strip()
    if cmd_upper.startswith(('G0', 'G1', 'G2', 'G3')):
        return True
    if any(axis in cmd_upper for axis in ('X', 'Y', 'Z', 'A')):
        return True
    return False

def handle_client(conn, addr):
    print(f"[+] Клиент: {addr}")
    with conn:
        while True:
            obj = receive_object(conn)
            if obj is None:
                break
            # obj - это строка команды
            cmd = obj.strip() if isinstance(obj, str) else str(obj)
            print(f"[{addr}] Команда: {cmd}")

            if cmd == "QUIT":
                send_object(conn, "BYE")
                break
            elif cmd == "GET_DRO":
                coords = read_dro()
                if coords:
                    resp = f"{coords[0]:.3f},{coords[1]:.3f},{coords[2]:.3f},{coords[3]:.3f}"
                else:
                    resp = "NO_DATA"
                send_object(conn, resp)
            else:
                if not write_command(cmd):
                    send_object(conn, "ERROR_WRITE")
                    continue
                if is_movement_command(cmd):
                    send_object(conn, "MOVING_STARTED")
                    final_coords = wait_for_movement_end()
                    if final_coords:
                        resp = f"{final_coords[0]:.3f},{final_coords[1]:.3f},{final_coords[2]:.3f},{final_coords[3]:.3f}"
                    else:
                        resp = "TIMEOUT"
                    send_object(conn, resp)
                else:
                    time.sleep(0.3)
                    coords = read_dro()
                    if coords:
                        resp =f"{coords[0]:.3f},{coords[1]:.3f},{coords[2]:.3f},{coords[3]:.3f}"
                    else:
                        resp = "OK"
                    send_object(conn, resp)
    print(f"[-] Клиент отключён: {addr}")

def start_server():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, PORT))
        s.listen()
        print(f"[Сервер строковых команд] {HOST}:{PORT}")
        try:
            while True:
                conn, addr = s.accept()
                threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
        except KeyboardInterrupt:
            print("\n[Сервер остановлен]")

if __name__ == "__main__":
    start_server()