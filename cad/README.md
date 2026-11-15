# 3D models of the robot body

Put the robot's printable parts here (STL for meshes, STEP/F3D for editable sources), one folder per module,
for example `chassis/`, `brain_unit/`, `screen_mount/`, `wheels/`. Add a short `parts.yaml` per folder:

```yaml
part: brain unit enclosure
holds: [orange pi zero, usb hub, webcam]
attaches_to: chassis top plate (LEGO-style studs)
printed: PLA, 0.2 mm, 20% infill
```

The robot reads this folder to learn its own shape (planned: render views and compare them with what its camera sees).
