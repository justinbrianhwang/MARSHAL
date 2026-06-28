"""CARLA 0.9.16 server process manager for unattended sweeps.

Launches CARLA_0.9.16/CarlaUE4.exe, waits until the RPC port answers and a
world is reachable, loads Town03, and can kill + relaunch the process between
sweep chunks (CARLA degrades after ~17 episodes; a load_world is not enough —
the PROCESS must be restarted).

Usage (from the marshal env):
    python scripts/_carla_manager.py restart   # kill any running server + start fresh + load Town03
    python scripts/_carla_manager.py status
    python scripts/_carla_manager.py kill
"""
from __future__ import annotations
import os, sys, time, socket, subprocess

THIS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(THIS, os.pardir))
CARLA_EXE = os.path.join(ROOT, "CARLA_0.9.16", "CarlaUE4.exe")
HOST, PORT = "127.0.0.1", 2000


def port_open(host: str = HOST, port: int = PORT, timeout: float = 1.5) -> bool:
    s = socket.socket(); s.settimeout(timeout)
    try:
        s.connect((host, port)); return True
    except Exception:
        return False
    finally:
        s.close()


def kill() -> None:
    # Windows: kill any CarlaUE4 process tree.
    for img in ("CarlaUE4-Win64-Shipping.exe", "CarlaUE4.exe"):
        subprocess.run(["taskkill", "/F", "/IM", img, "/T"],
                       capture_output=True, text=True)
    # wait for the port to actually close
    for _ in range(30):
        if not port_open():
            break
        time.sleep(0.5)


def launch(quality: str = "Epic") -> subprocess.Popen:
    if not os.path.exists(CARLA_EXE):
        raise FileNotFoundError(CARLA_EXE)
    cmd = [CARLA_EXE, f"-carla-rpc-port={PORT}", f"-quality-level={quality}",
           "-windowed", "-ResX=640", "-ResY=480"]
    # detached so it survives this launcher process
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS
    return subprocess.Popen(cmd, cwd=ROOT, creationflags=flags,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_ready(timeout: float = 90.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if port_open():
            return True
        time.sleep(1.0)
    return False


def load_town03(timeout: float = 120.0) -> str:
    sys.path.insert(0, ROOT)
    from marshal_bench.utils.carla_api_compat import import_carla
    carla = import_carla()
    c = carla.Client(HOST, PORT); c.set_timeout(timeout)
    # give the freshly-started engine a moment to accept the world load
    last = None
    for _ in range(5):
        try:
            c.load_world("Town03")
            w = c.get_world()
            return w.get_map().name
        except Exception as e:
            last = e; time.sleep(3.0)
    raise RuntimeError(f"load_world(Town03) failed: {last}")


def restart(quality: str = "Epic") -> str:
    kill()
    time.sleep(2.0)
    launch(quality)
    if not wait_ready(120.0):
        raise RuntimeError("CARLA did not open RPC port within timeout")
    time.sleep(4.0)  # let the default map finish loading before switching
    name = load_town03()
    return name


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print("port_open:", port_open())
    elif cmd == "kill":
        kill(); print("killed; port_open:", port_open())
    elif cmd == "launch":
        launch(); print("launched; waiting..."); print("ready:", wait_ready()); print("map:", load_town03())
    elif cmd == "restart":
        print("restarting CARLA ...")
        name = restart()
        print("RESTARTED. map:", name, "port_open:", port_open())
    else:
        print("unknown cmd", cmd)
