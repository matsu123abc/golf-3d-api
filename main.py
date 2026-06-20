import os
import json
import logging
import xml.etree.ElementTree as ET

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from azure.storage.blob import BlobServiceClient

import numpy as np
from scipy.interpolate import griddata

app = FastAPI()
logger = logging.getLogger("green_app")

# ---------------------------------------------------
# Blob 接続（green-svg に固定）
# ---------------------------------------------------
connection_string = os.getenv("BLOB_CONNECTION_STRING")
container_name = "green-svg"   # コンテナを green-svg に統一
blob_service = BlobServiceClient.from_connection_string(connection_string)
container_client = blob_service.get_container_client(container_name)

# ---------------------------------------------------
# class → stroke 色のマッピング
# ---------------------------------------------------
CLASS_TO_STROKE = {
    "a": "#ff0000",     # 赤（最も高い）
    "b": "#ffa500",     # オレンジ
    "c": "#ffff00",     # 黄
    "d": "#00ff00",     # 緑
    "s0": "#ff00ff",    # Edge（境界）
}

# ---------------------------------------------------
# Blob から SVG テキストを読み込む
# ---------------------------------------------------
def load_svg_from_blob(blob_name: str) -> str:
    try:
        blob = container_client.get_blob_client(blob_name)
        raw = blob.download_blob().readall()
        return raw.decode("utf-8")
    except Exception:
        logger.exception(f"Failed to load SVG from blob: {blob_name}")
        raise HTTPException(status_code=500, detail=f"{blob_name} の読み込みに失敗しました")

# ---------------------------------------------------
# stroke / class → 高さ値（0〜3）
# ---------------------------------------------------
def stroke_to_height(stroke_or_class: str) -> int:
    if not stroke_or_class:
        return 0

    key = stroke_or_class.lower()

    if key in CLASS_TO_STROKE:
        key = CLASS_TO_STROKE[key]

    if key == "#ff0000":   # 赤
        return 3
    if key == "#ffa500":   # オレンジ
        return 2
    if key == "#ffff00":   # 黄
        return 1
    if key == "#00ff00":   # 緑
        return 0
    if key == "#ff00ff":   # Edge
        return 0

    return 0

# ---------------------------------------------------
# SVG → 36×36 高さマップ生成（linear + nearest 補完）
# ---------------------------------------------------
def generate_height_map_from_svg(svg_text: str):
    root = ET.fromstring(svg_text)

    all_points = []

    for path in root.findall(".//{http://www.w3.org/2000/svg}path"):
        d = path.attrib.get("d")
        if not d:
            continue

        import re
        coords = re.findall(r"([0-9]+\.?[0-9]*)[, ]+([0-9]+\.?[0-9]*)", d)

        for x_str, y_str in coords:
            all_points.append((float(x_str), float(y_str)))

    if not all_points:
        raise HTTPException(status_code=500, detail="SVG から座標が取得できませんでした")

    xs = [p[0] for p in all_points]
    ys = [p[1] for p in all_points]

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    grid_w = 36
    grid_h = 36

    sample_points = []
    sample_heights = []

    for path in root.findall(".//{http://www.w3.org/2000/svg}path"):

        stroke_or_class = path.attrib.get("stroke") or path.attrib.get("class")
        height = stroke_to_height(stroke_or_class)

        d = path.attrib.get("d")
        if not d:
            continue

        import re
        coords = re.findall(r"([0-9]+\.?[0-9]*)[, ]+([0-9]+\.?[0-9]*)", d)

        for x_str, y_str in coords:
            x = float(x_str)
            y = float(y_str)

            gx = (x - min_x) / (max_x - min_x)
            gy = (y - min_y) / (max_y - min_y)

            sample_points.append([gx, gy])
            sample_heights.append(height)

    sample_points = np.array(sample_points)
    sample_heights = np.array(sample_heights)

    grid_x, grid_y = np.meshgrid(
        np.linspace(0, 1, grid_w),
        np.linspace(0, 1, grid_h)
    )

    grid_z = griddata(sample_points, sample_heights, (grid_x, grid_y), method="linear")
    grid_z2 = griddata(sample_points, sample_heights, (grid_x, grid_y), method="nearest")
    nan_mask = np.isnan(grid_z)
    grid_z[nan_mask] = grid_z2[nan_mask]

    grid_z = np.clip(np.rint(grid_z), 0, 3).astype(int)

    return grid_z.tolist()

# ---------------------------------------------------
# API: SVG → JSON 生成（green_1.json）
# ---------------------------------------------------
@app.get("/generate/green/svg/1")
def generate_green_from_svg():
    contour_svg = load_svg_from_blob("contour.svg")
    edge_svg = load_svg_from_blob("edge.svg")  # 今後の境界処理用（現状未使用）

    height_map = generate_height_map_from_svg(contour_svg)

    json_data = {
        "green_id": 1,
        "grid_width": 36,
        "grid_height": 36,
        "cell_size_yards": 1.0,
        "heights": height_map,
        "pin_positions": {}
    }

    try:
        blob = container_client.get_blob_client("green_1.json")
        blob.upload_blob(json.dumps(json_data), overwrite=True)
    except Exception:
        logger.exception("Failed to upload green_1.json")
        raise HTTPException(status_code=500, detail="green_1.json のアップロードに失敗しました")

    return {"status": "green_1.json generated from SVG"}

# ---------------------------------------------------
# 起動画面：統合 UI（18ホール対応）
# ---------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def green_ui():
    return """
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>Green - ピン登録 & AI戦略</title>
<style>
  body { background:#222; color:white; font-size:20px; text-align:center; margin:0; padding:10px; }
  #canvas { touch-action: manipulation; border:1px solid #555; }
  button {
    font-size:22px; padding:12px 24px; margin-top:10px;
    background:#4CAF50; border:none; color:white; border-radius:6px;
    width:90%;
  }
  #result {
    margin-top:20px; padding:15px; background:#333; border-radius:8px;
    white-space:pre-wrap; text-align:left;
  }
  iframe {
    width:100%;
    height:400px;
    border:1px solid #555;
    border-radius:8px;
    margin-top:20px;
  }
</style>
</head>
<body>

<h2>Green - ピン登録 & AI戦略</h2>

<div style="margin: 10px;">
  <label for="holeSelect">ホール選択：</label>
  <select id="holeSelect" style="font-size:20px; padding:4px;">
    <option value="1">Hole 1</option>
    <option value="2">Hole 2</option>
    <option value="3">Hole 3</option>
    <option value="4">Hole 4</option>
    <option value="5">Hole 5</option>
    <option value="6">Hole 6</option>
    <option value="7">Hole 7</option>
    <option value="8">Hole 8</option>
    <option value="9">Hole 9</option>
    <option value="10">Hole 10</option>
    <option value="11">Hole 11</option>
    <option value="12">Hole 12</option>
    <option value="13">Hole 13</option>
    <option value="14">Hole 14</option>
    <option value="15">Hole 15</option>
    <option value="16">Hole 16</option>
    <option value="17">Hole 17</option>
    <option value="18">Hole 18</option>
  </select>
</div>

<canvas id="canvas" width="360" height="360"></canvas>

<p id="info">ピン位置をタップしてください</p>

<button id="saveBtn" style="display:none;">この位置を登録する</button>
<button id="aiBtn" style="display:none; background:#2196F3;">AI に戦略を聞く</button>

<div id="result"></div>

<h3 style="margin-top:30px;">3D グリーン（参考表示）</h3>
<iframe id="view3d" src="/green/1/3d"></iframe>

<script>
let selectedX = null;
let selectedY = null;
let currentHole = 1;

let greenImageUrl = "https://pcbdiagnosisrga8a5.blob.core.windows.net/green-svg/green_1.png";

const holeSelect = document.getElementById("holeSelect");
const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");
const saveBtn = document.getElementById("saveBtn");
const aiBtn = document.getElementById("aiBtn");
const iframe = document.getElementById("view3d");

const img = new Image();
img.src = greenImageUrl;
img.onload = () => {
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
};

holeSelect.addEventListener("change", function() {
    currentHole = parseInt(this.value);

    greenImageUrl = "https://pcbdiagnosisrga8a5.blob.core.windows.net/green-svg/green_" + currentHole + ".png";
    img.src = greenImageUrl;

    iframe.src = "/green/" + currentHole + "/3d";

    selectedX = null;
    selectedY = null;
    saveBtn.style.display = "none";
    aiBtn.style.display = "none";
    document.getElementById("info").innerText = "ピン位置をタップしてください";
    document.getElementById("result").innerText = "";

    img.onload = () => {
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    };
});

canvas.addEventListener("click", function(e) {
    const rect = canvas.getBoundingClientRect();
    selectedX = Math.floor((e.clientX - rect.left) / 10);
    selectedY = Math.floor((e.clientY - rect.top) / 10);

    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    ctx.beginPath();
    ctx.arc(selectedX * 10, selectedY * 10, 6, 0, Math.PI * 2);
    ctx.fillStyle = "red";
    ctx.fill();

    document.getElementById("info").innerText =
        `選択中のピン位置: (${selectedX}, ${selectedY})`;

    saveBtn.style.display = "block";
});

saveBtn.addEventListener("click", async function() {
    await fetch("/set_pin/" + currentHole, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ x: selectedX, y: selectedY })
    });

    document.getElementById("info").innerText =
        "ピン位置 (" + selectedX + ", " + selectedY + ") を登録しました！";
    
    aiBtn.style.display = "block";
});

aiBtn.addEventListener("click", async function() {
    document.getElementById("result").innerText = "AI が戦略を計算中です…";

    const res = await fetch("/ai_strategy/" + currentHole, { method: "POST" });
    if (!res.ok) {
      const text = await res.text();
      document.getElementById("result").innerText = "サーバーエラー: " + text;
      return;
    }

    const data = await res.json();

    if (!data.slope_analysis || !data.strategy) {
      document.getElementById("result").innerText = "レスポンス形式が不正です";
      return;
    }

    let text = "";
    text += "⛰️ 傾斜の解説:\\n" + data.slope_analysis + "\\n\\n";
    text += "🧠 戦略:\\n" + data.strategy;

    document.getElementById("result").innerText = text;

    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    ctx.beginPath();
    ctx.arc(selectedX * 10, selectedY * 10, 6, 0, Math.PI * 2);
    ctx.fillStyle = "red";
    ctx.fill();
});
</script>

</body>
</html>
"""

# ---------------------------------------------------
# 3D 表示（汎用：1〜18）
# ---------------------------------------------------
@app.get("/green/{green_id}/3d", response_class=HTMLResponse)
def green_3d(green_id: int):
    return f"""
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>Green {green_id} - 3D View</title>
<style>
  body {{ margin: 0; overflow: hidden; background: #222; }}
  canvas {{ display: block; }}
</style>
</head>
<body>

<script type="module">
import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.152.2/build/three.module.js";
import {{ OrbitControls }} from "https://cdn.jsdelivr.net/npm/three@0.152.2/examples/jsm/controls/OrbitControls.js";

async function loadGreenData() {{
  const url = "https://pcbdiagnosisrga8a5.blob.core.windows.net/green-svg/green_1.json";
  const res = await fetch(url);
  if (!res.ok) {{
    console.error("JSON load error:", res.status);
    return null;
  }}
  return await res.json();
}}

async function main() {{
  const data = await loadGreenData();
  if (!data) {{
    document.body.innerHTML = "<p style='color:white'>JSON 読み込み失敗</p>";
    return;
  }}

  const heights = data.heights;
  const W = data.grid_width;
  const H = data.grid_height;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x222222);

  const camera = new THREE.PerspectiveCamera(
    45,
    window.innerWidth / window.innerHeight,
    0.1,
    1000
  );
  camera.position.set(0, -60, 40);
  camera.lookAt(0, 0, 0);

  const renderer = new THREE.WebGLRenderer({{ antialias: true }});
  renderer.setSize(window.innerWidth, window.innerHeight);
  document.body.appendChild(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 0, 0);
  controls.update();

  const light = new THREE.DirectionalLight(0xffffff, 1);
  light.position.set(50, -50, 80);
  scene.add(light);

  const ambient = new THREE.AmbientLight(0x888888);
  scene.add(ambient);

  const geometry = new THREE.PlaneGeometry(36, 36, W - 1, H - 1);
  const verts = geometry.attributes.position;

  for (let i = 0; i < verts.count; i++) {{
    const x = i % W;
    const y = Math.floor(i / W);
    const h = heights[y][x] * 0.5;
    verts.setZ(i, h);
  }}
  verts.needsUpdate = true;
  geometry.computeVertexNormals();

  const material = new THREE.MeshStandardMaterial({{
    color: 0x228b22,
    roughness: 0.9,
    metalness: 0.0,
    side: THREE.DoubleSide
  }});

  const mesh = new THREE.Mesh(geometry, material);
  mesh.rotation.x = -Math.PI / 2;
  scene.add(mesh);

  function animate() {{
    requestAnimationFrame(animate);
    renderer.render(scene, camera);
  }}
  animate();

  window.addEventListener("resize", () => {{
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  }});
}}

main().catch(e => {{
  console.error("3D error:", e);
}});
</script>

</body>
</html>
"""
