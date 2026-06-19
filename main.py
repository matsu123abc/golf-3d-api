import os
import json
import logging
import xml.etree.ElementTree as ET

from fastapi import FastAPI, HTTPException
from azure.storage.blob import BlobServiceClient

app = FastAPI()
logger = logging.getLogger("green_app")

connection_string = os.getenv("BLOB_CONNECTION_STRING")
container_name = os.getenv("GREEN_CONTAINER_NAME")   # green-svg
blob_service = BlobServiceClient.from_connection_string(connection_string)
container_client = blob_service.get_container_client(container_name)

# ---------------------------------------------------
# Blob から SVG テキストを読み込む（URL ではなく Blob 名）
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
# SVG の path の stroke 色 → 高さ値
# ---------------------------------------------------
def stroke_to_height(stroke: str) -> int:
    if stroke is None:
        return 0

    stroke = stroke.lower()

    if stroke == "#ff0000":   # 赤
        return 7
    if stroke == "#ffa500":   # オレンジ
        return 6
    if stroke == "#ffff00":   # 黄
        return 4
    if stroke == "#00ff00":   # 緑
        return 2
    if stroke == "#0000ff":   # 青
        return 0
    if stroke == "#ff00ff":   # Edge（境界）
        return 0

    return 0

# ---------------------------------------------------
# SVG → 36×36 高さマップ生成
# ---------------------------------------------------
def generate_height_map_from_svg(svg_text: str):
    root = ET.fromstring(svg_text)

    # SVG の viewBox を取得
    viewbox = root.attrib.get("viewBox", "0 0 2123 3857")
    _, _, w, h = map(float, viewbox.split())

    grid_w = 36
    grid_h = 36

    height_map = [[0 for _ in range(grid_w)] for _ in range(grid_h)]

    for path in root.findall(".//{http://www.w3.org/2000/svg}path"):
        stroke = path.attrib.get("stroke") or path.attrib.get("class")
        height = stroke_to_height(stroke)

        d = path.attrib.get("d")
        if not d:
            continue

        import re
        coords = re.findall(r"([0-9]+\.?[0-9]*)[, ]+([0-9]+\.?[0-9]*)", d)

        for x_str, y_str in coords:
            x = float(x_str)
            y = float(y_str)

            gx = int((x / w) * grid_w)
            gy = int((y / h) * grid_h)

            if 0 <= gx < grid_w and 0 <= gy < grid_h:
                height_map[gy][gx] = max(height_map[gy][gx], height)

    return height_map

# ---------------------------------------------------
# API: SVG → JSON 生成
# ---------------------------------------------------
@app.get("/generate/green/svg/1")
def generate_green_from_svg():

    # Blob から直接読み込む
    contour_svg = load_svg_from_blob("contour.svg")
    edge_svg = load_svg_from_blob("edge.svg")  # 今は未使用だが後で境界処理に使える

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
