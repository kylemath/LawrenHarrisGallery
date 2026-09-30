#!/usr/bin/env python3
"""Color-space map of the Harris images.

Each painting is described in CIE Lab (perceptually uniform), not raw RGB:
mean lightness and chroma, a chroma-weighted hue histogram, the share of
near-gray pixels, and warm / cool / green area. Those features are clustered
with k-means. A t-SNE embedding places each image in 2D so similar palettes
sit together. docs/visual.js is what the gallery's color map reads.

  source .venv/bin/activate && python analyze_visual.py
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from sklearn.cluster import KMeans
from sklearn.manifold import TSNE
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "docs" / "catalog.js"
OUT = ROOT / "docs" / "visual.js"


def srgb_to_lab(rgb):
    a = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m = np.array([
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ])
    xyz = a @ m.T
    xyz /= np.array([0.95047, 1.0, 1.08883])
    delta = 6 / 29
    f = np.where(xyz > delta ** 3, np.cbrt(xyz), xyz / (3 * delta ** 2) + 4 / 29)
    L = 116 * f[..., 1] - 16
    A = 500 * (f[..., 0] - f[..., 1])
    B = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, A, B], axis=-1)


def features_of(path):
    im = Image.open(path).convert("RGB")
    im.thumbnail((72, 72))
    rgb = np.asarray(im, dtype=np.float32) / 255.0
    lab = srgb_to_lab(rgb)
    L, A, B = lab[..., 0], lab[..., 1], lab[..., 2]
    chroma = np.hypot(A, B)
    hue = np.arctan2(B, A)  # -pi..pi, 0 = red, +pi/2 = yellow, -pi/2 = blue
    weight = chroma.ravel()
    hist, _ = np.histogram(hue.ravel(), bins=8, range=(-np.pi, np.pi), weights=weight)
    hist = hist / (hist.sum() + 1e-6)
    gray = float((chroma < 10).mean())
    warm = float(((A > 6) & (B > 8) & (chroma > 12)).mean())
    cool = float(((B < -6) & (chroma > 10)).mean())
    green = float(((A < -6) & (B > -4) & (chroma > 10)).mean())
    h = L.shape[0]
    sky = float(L[: max(1, h // 3)].mean() - L[-(max(1, h // 3)):].mean())
    mean_rgb = rgb.reshape(-1, 3).mean(axis=0)
    return {
        "vec": np.concatenate([
            [L.mean() / 100, A.mean() / 40, B.mean() / 40, L.std() / 40, chroma.mean() / 40],
            [gray, warm, cool, green, sky / 40],
            hist,
        ]),
        "L": float(L.mean()),
        "a": float(A.mean()),
        "b": float(B.mean()),
        "chroma": float(chroma.mean()),
        "gray": gray,
        "rgb": mean_rgb,
    }


def hex_of(rgb):
    v = np.clip(np.round(rgb * 255), 0, 255).astype(int)
    return "#{:02x}{:02x}{:02x}".format(*v)


def name_cluster(stats):
    L, a, b = stats["L"], stats["a"], stats["b"]
    if stats["gray"] > 0.42 and L > 65:
        return "Graphite sketches"
    if b < -8:
        return "Arctic blue"
    if L < 42 and abs(a) < 4 and abs(b) < 4:
        return "Charcoal and shadow"
    if a < -2 and b > 8:
        return "Olive green"
    if a > 3 and b > 14:
        return "Ochre and rock"
    if b > 6 and abs(a) < 6:
        return "Warm gray"
    if b < 0 and a < 4:
        return "Cool teal"
    if abs(a) < 8 and abs(b) < 8:
        return "Neutral stone"
    if L < 34:
        return "Nocturnes"
    return "Mixed palette"


def main():
    raw = CATALOG.read_text().split("=", 1)[1].strip().rstrip(";")
    images = json.loads(raw)["images"]
    rows = []
    for im in images:
        if not im.get("primary"):
            continue
        path = (ROOT / "docs" / im["src"]).resolve()
        if not path.exists():
            continue
        try:
            feat = features_of(path)
        except Exception as e:
            print("skip", im["id"], e)
            continue
        feat["id"] = im["id"]
        rows.append(feat)
    print(f"measured {len(rows)} images")
    X = np.stack([r["vec"] for r in rows])
    Xs = StandardScaler().fit_transform(X)
    best_k, best_s = 6, -1
    for k in range(4, 9):
        labels = KMeans(n_clusters=k, n_init=10, random_state=0).fit_predict(Xs)
        score = silhouette_score(Xs, labels)
        print(f"  k={k} silhouette={score:.3f}")
        if score > best_s:
            best_k, best_s = k, score
    labels = KMeans(n_clusters=best_k, n_init=20, random_state=0).fit_predict(Xs)
    xy = TSNE(n_components=2, perplexity=30, init="pca", learning_rate="auto", random_state=0).fit_transform(Xs)
    xy = (xy - xy.min(0)) / (xy.max(0) - xy.min(0) + 1e-9)

    clusters = []
    used = {}
    for k in range(best_k):
        members = [rows[i] for i in range(len(rows)) if labels[i] == k]
        stats = {
            "L": float(np.mean([m["L"] for m in members])),
            "a": float(np.mean([m["a"] for m in members])),
            "b": float(np.mean([m["b"] for m in members])),
            "chroma": float(np.mean([m["chroma"] for m in members])),
            "gray": float(np.mean([m["gray"] for m in members])),
        }
        name = name_cluster(stats)
        used[name] = used.get(name, 0) + 1
        if used[name] > 1:
            name = f"{name} {used[name]}"
        swatch = hex_of(np.mean([m["rgb"] for m in members], axis=0))
        clusters.append({"id": k, "name": name, "color": swatch, "n": len(members)})
        print(f"  {name:20} n={len(members):3}  L={stats['L']:.0f} a={stats['a']:.0f} b={stats['b']:.0f}  {swatch}")

    points = [
        {"id": rows[i]["id"], "x": round(float(xy[i, 0]), 4), "y": round(float(xy[i, 1]), 4), "cluster": int(labels[i])}
        for i in range(len(rows))
    ]
    payload = {
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "method": (
            "CIE Lab palette features (lightness, chroma, hue histogram, gray share, warm/cool/green), "
            f"k-means (k={best_k}, silhouette {best_s:.2f}), t-SNE embedding. "
            "The map packs each painting near its point with a bubble collision pass."
        ),
        "clusters": clusters,
        "points": points,
    }
    OUT.write_text("window.VISUAL = " + json.dumps(payload, ensure_ascii=False) + ";\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
