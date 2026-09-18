import rtmidi
from s3k.bridge import S3kBridge


def connect(port_name):
    print(f"Attempting to connect to '{port_name}'...")
    bridge = S3kBridge.standard(port_name)
    print("Ports opened succesfully - bridge object created.")
    return bridge


bridge = connect("IAC Driver Bus 1")

print("Trying a real request (should time out cleanly, nothing is listening)...")
try:
    import s3k.params as p

    bridge.get_parameter(p.lookup("PRNAME", "program"), 0)
except TimeoutError as e:
    print(f"Got the expected timeout: {e}")
