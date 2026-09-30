#!/usr/bin/env python3
"""Style map of the Harris images.

Zero-shot CLIP (ViT-B-32) scores each primary image against three prompt
groups: line drawing, abstract painting, and landscape or city painting.
The three probabilities become a ternary plot (drawings lower left, abstracts
lower right, landscape and city toward the top). A catalog prior only nudges
works whose title or medium already says drawing or abstract — oil sketches
stay with the image score, since many of those are landscapes.

    source .venv/bin/activate && python analyze_style.py
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import open_clip
import torch
from PIL import Image
from sklearn.decomposition import PCA

ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "docs" / "catalog.js"
OUT = ROOT / "docs" / "style.js"

CLASSES = [
    {"id": 0, "name": "Line drawings", "color": "#8d8478"},
    {"id": 1, "name": "Abstract", "color": "#7a4e78"},
    {"id": 2, "name": "Landscape and city", "color": "#1e4660"},
]

PROMPTS = [
    [
        "a monochrome graphite pencil drawing on white paper",
        "a charcoal or ink line drawing, uncolored contour lines on paper",
        "a pencil sketch on paper with visible strokes and no paint",
        "a drawing in graphite on paper, not an oil painting",
    ],
    [
        "a non-representational abstract painting of flat geometric color shapes",
        "an abstract painting with no trees, no houses, no horizon, and no realistic landscape",
        "a hard-edge abstract composition of simplified color planes",
        "a non-objective modernist painting of biomorphic or geometric forms",
    ],
    [
        "an oil painting of a landscape with mountains, lake, forest, snow, or ice",
        "a landscape painting of nature, trees, rocks, or a northern shore",
        "a cityscape painting of houses, streets, or buildings in a town",
        "an oil painting of an urban street or houses in winter",
        "a representational painting of a real place, either wilderness or a city",
    ],
]

DRAWING = re.compile(
    r"\b(graphite|pencil|charcoal|conte|crayon|pen and ink|ink on paper|drawing)\b",
    re.I,
)
OIL = re.compile(r"\boil\b|canvas|acrylic|tempera|watercolou?r", re.I)
ABSTRACT = re.compile(r"\babstract", re.I)


def catalog_images():
    raw = CATALOG.read_text().split("=", 1)[1].strip().rstrip(";")
    return json.loads(raw)["images"]


def priors(title, medium):
    """Logit nudges. Oil sketches are not treated as line drawings."""
    title = title or ""
    medium = medium or ""
    bonus = np.zeros(3, dtype=np.float32)
    if ABSTRACT.search(title) or ABSTRACT.search(medium):
        bonus[1] += 4.0
    painted = bool(OIL.search(medium))
    if not painted and (DRAWING.search(medium) or DRAWING.search(title)):
        bonus[0] += 4.0
    return bonus


def encode_texts(model, tokenizer):
    weights = []
    with torch.inference_mode():
        for prompts in PROMPTS:
            tokens = tokenizer(prompts)
            feat = model.encode_text(tokens)
            feat = feat / feat.norm(dim=-1, keepdim=True)
            mean = feat.mean(dim=0)
            mean = mean / mean.norm()
            weights.append(mean)
    return torch.stack(weights, dim=1)


def main():
    device = "cpu"
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="openai", device=device
    )
    model.eval()
    tokenizer = open_clip.get_tokenizer("ViT-B-32")
    text = encode_texts(model, tokenizer)
    scale = float(model.logit_scale.exp().detach())
    print(f"logit scale {scale:.1f}")

    rows = []
    batch = []
    for im in catalog_images():
        if not im.get("primary"):
            continue
        path = (ROOT / "docs" / im["src"]).resolve()
        if not path.exists():
            continue
        try:
            image = preprocess(Image.open(path).convert("RGB"))
        except Exception as e:
            print("skip", im["id"], e)
            continue
        batch.append((im, image))
        if len(batch) == 24:
            rows.extend(score_batch(model, text, scale, batch))
            batch = []
            print(f"  scored {len(rows)}")
    if batch:
        rows.extend(score_batch(model, text, scale, batch))
    print(f"scored {len(rows)} images")

    counts = [0, 0, 0]
    conf = []
    for row in rows:
        counts[row["cluster"]] += 1
        conf.append(row["probs"][row["cluster"]])
        if row["probs"][row["cluster"]] < 0.45 or ABSTRACT.search(row["title"] or "") or DRAWING.search((row["medium"] or "") + " " + (row["title"] or "")):
            probs = " ".join(f"{p:.2f}" for p in row["probs"])
            print(f"  {CLASSES[row['cluster']]['name'][:12]:12} {probs}  {(row['title'] or '')[:64]}")
    print("counts", {CLASSES[i]["name"]: counts[i] for i in range(3)}, "mean conf", round(float(np.mean(conf)), 3))

    # Within each style, PCA of the CLIP embedding fills that style's region
    # so similar paintings sit together instead of stacking on one point.
    for k in range(3):
        idx = [i for i, row in enumerate(rows) if row["cluster"] == k]
        if len(idx) < 3:
            for i in idx:
                rows[i]["u"] = 0.5
                rows[i]["v"] = 0.5
            continue
        local = PCA(n_components=2, random_state=0).fit_transform(
            np.stack([rows[i]["feat"] for i in idx])
        )
        local = (local - local.min(0)) / (np.ptp(local, axis=0) + 1e-9)
        for i, (u, v) in zip(idx, local):
            rows[i]["u"] = float(u)
            rows[i]["v"] = float(v)

    for c in CLASSES:
        c["n"] = counts[c["id"]]
    points = [
        {
            "id": row["id"],
            "x": round(row["x"], 4),
            "y": round(row["y"], 4),
            "u": round(row["u"], 4),
            "v": round(row["v"], 4),
            "cluster": row["cluster"],
            "probs": [round(float(p), 4) for p in row["probs"]],
        }
        for row in rows
    ]
    payload = {
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "method": (
            "CLIP ViT-B-32 zero-shot (OpenAI weights). Each image is scored against "
            "prompt ensembles for line drawing, abstract painting, and landscape or city painting. "
            "Drawings sit lower left, abstracts lower right, landscape and city toward the top. "
            "A small PCA of the same embedding spreads each style into its own cloud."
        ),
        "clusters": CLASSES,
        "points": points,
    }
    OUT.write_text("window.STYLE = " + json.dumps(payload, ensure_ascii=False) + ";\n")
    print(f"wrote {OUT}")


def score_batch(model, text, scale, batch):
    images = torch.stack([item[1] for item in batch])
    with torch.inference_mode():
        feat = model.encode_image(images)
        feat = feat / feat.norm(dim=-1, keepdim=True)
        logits = feat @ text * scale
    vectors = feat.cpu().numpy()
    out = []
    for (im, _), logit, vec in zip(batch, logits.cpu().numpy(), vectors):
        logit = logit + priors(im.get("title"), im.get("medium"))
        logit = logit - logit.max()
        exp = np.exp(logit)
        probs = exp / exp.sum()
        cluster = int(probs.argmax())
        p_line, p_abs, p_scene = (float(probs[0]), float(probs[1]), float(probs[2]))
        out.append({
            "id": im["id"],
            "title": im.get("title"),
            "medium": im.get("medium"),
            "cluster": cluster,
            "probs": probs,
            "x": p_abs + 0.5 * p_scene,
            "y": 1.0 - p_scene,
            "feat": vec,
        })
    return out


if __name__ == "__main__":
    main()
