"""Unmute all active capture (microphone) endpoints + raise low volume, then report."""
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import (AudioUtilities, IAudioEndpointVolume,
                         IMMDeviceCollection, EDataFlow, DEVICE_STATE)

enum = AudioUtilities.GetDeviceEnumerator()
coll = enum.EnumAudioEndpoints(EDataFlow.eCapture.value, DEVICE_STATE.ACTIVE.value)
n = coll.GetCount()
print(f"capture endpoints: {n}")
for i in range(n):
    dev = coll.Item(i)
    try:
        ad = AudioUtilities.CreateDevice(dev)
        name = ad.FriendlyName
    except Exception:
        name = f"endpoint {i}"
    ep = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    vol = cast(ep, POINTER(IAudioEndpointVolume))
    muted = bool(vol.GetMute())
    level = vol.GetMasterVolumeLevelScalar()
    changes = []
    if muted:
        vol.SetMute(False, None)
        changes.append("UNMUTED")
    if level < 0.10:
        vol.SetMasterVolumeLevelScalar(0.70, None)
        changes.append("volume->70%")
        level = vol.GetMasterVolumeLevelScalar()
    print(f"  [{i}] {name}: mute={muted} vol={level:.0%} -> {' '.join(changes) or 'no change'}")
