#!/bin/sh
set -e
cd "$(dirname "$0")"
HEIGHT=${1:-1080}
BLENDER=${BLENDER:-blender}
FRAMES="$PWD/output/frames_${HEIGHT}p"
OUT="output/car-driving_${HEIGHT}p24.mp4"

mkdir -p "$FRAMES"
"$BLENDER" -b --factory-startup -P build_scene.py \
  --python-expr "import bpy; r = bpy.context.scene.render; r.resolution_percentage = round($HEIGHT * 100 / 720); r.filepath = '$FRAMES/'" \
  -a
ffmpeg -y -framerate 24 -i "$FRAMES/%04d.png" -filter_complex_script audio/ambience.ffgraph \
  -map 0:v -map "[a]" -c:v libx264 -crf 16 -preset slow -pix_fmt yuv420p -c:a aac -b:a 192k -shortest "$OUT"
echo "$OUT"
