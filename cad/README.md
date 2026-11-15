# 3D models of the robot body

Put the robot's printable parts here (STL for meshes, STEP/F3D for editable sources), one folder per module,
for example `chassis/`, `brain_unit/`, `screen_mount/`, `wheels/`. Add a short `parts.yaml` per folder:

```yaml
part: brain unit enclosure
holds: [orange pi zero, usb hub, webcam]
attaches_to: chassis top plate (LEGO-style studs)
printed: PLA, 0.2 mm, 20% infill
```

Boards are held on the frame by printed stud plates that click into the 8 mm grid. Make one for any board with the
LEGO-ifier (`site/legoifier.html`): calibrate first, then download the STL. How the boards are bolted or glued on:
`docs/MOUNTING_BOARDS.md`.

The robot reads this folder to learn its own shape (planned: render views and compare them with what its camera sees).
