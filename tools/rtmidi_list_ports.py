import rtmidi

midi_out = rtmidi.MidiOut()
print("Available outputs:")
for i, name in enumerate(midi_out.get_ports()):
    print(f"  {i}: {name}")
