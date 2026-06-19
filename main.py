import os
import json
import logging
import xml.etree.ElementTree as ET

from fastapi import FastAPI, HTTPException
from azure.storage.blob import BlobServiceClient

app = FastAPI()
logger = logging.getLogger("green_app")

# ---------------------------------------------------
# Blob 接続
# ---------------------------------------------------
connection_string = os.getenv("BLOB_CONNECTION_STRING")
container_name = os.getenv("GREEN_CONTAINER_NAME")   # green-svg
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
# stroke / class → 高さ値
# ---------------------------------------------------
def stroke_to_height(stroke_or_class: str) -> int:
    if not stroke_or_class:
        return 0

    key = stroke_or_class.lower()

    # class → stroke 色に変換
    if key in CLASS_TO_STROKE:
        key = CLASS_TO_STROKE[key]

    # ★ 高さレンジを 0〜3 に圧縮
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
# SVG → 36×36 高さマップ生成
# ---------------------------------------------------
import numpy as np
from scipy.interpolate import griddata

def generate_height_map_from_svg(svg_text: str):
    root = ET.fromstring(svg_text)

    # すべての path の座標を一度集める（min/max を求めるため）
    all_points = []

    for path in root.findall(".//{http://www.w3.org/2000/svg}path"):
        d = path.attrib.get("d")
        if not d:
            continue

        import re
        coords = re.findall(r"([0-9]+\.?[0-9]*)[, ]+([0-9]+\.?[0-9]*)", d)

        for x_str, y_str in coords:
            all_points.append((float(x_str), float(y_str)))

    # min/max を計算
    xs = [p[0] for p in all_points]
    ys = [p[1] for p in all_points]

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    grid_w = 36
    grid_h = 36

    # 等高線上の点（高さ付き）を集める
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

            # 正規化（min/max を使う）
            gx = (x - min_x) / (max_x - min_x)
            gy = (y - min_y) / (max_y - min_y)

            sample_points.append([gx, gy])
            sample_heights.append(height)

    sample_points = np.array(sample_points)
    sample_heights = np.array(sample_heights)

    # 補間用のグリッド
    grid_x, grid_y = np.meshgrid(
        np.linspace(0, 1, grid_w),
        np.linspace(0, 1, grid_h)
    )

    # ★ linear 補間（安定）
    grid_z = griddata(sample_points, sample_heights, (grid_x, grid_y), method="linear")

    # ★ linear で埋まらなかった部分を nearest で補完（自然な外周になる）
    grid_z2 = griddata(sample_points, sample_heights, (grid_x, grid_y), method="nearest")
    nan_mask = np.isnan(grid_z)
    grid_z[nan_mask] = grid_z2[nan_mask]

    # int に丸める（0〜3）
    grid_z = np.clip(np.rint(grid_z), 0, 3).astype(int)

    return grid_z.tolist()



# ---------------------------------------------------
# API: SVG → JSON 生成
# ---------------------------------------------------
@app.get("/generate/green/svg/1")
def generate_green_from_svg():

    # Blob から SVG を読み込む
    contour_svg = load_svg_from_blob("contour.svg")
    edge_svg = load_svg_from_blob("edge.svg")  # 今後の境界処理用（現状未使用）

    # 高さマップ生成
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
        blob = container_client.get_blob_client("green_svg_1.json")
        blob.upload_blob(json.dumps(json_data), overwrite=True)
    except Exception:
        logger.exception("Failed to upload green_svg_1.json")
        raise HTTPException(status_code=500, detail="green_svg_1.json のアップロードに失敗しました")

    return {"status": "green_svg_1.json generated from SVG"}
