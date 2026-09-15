# Training data

Large images are not committed. Train on **Colab T4** with:

`notebooks/train_india_road_yolov8.ipynb`

```text
data/
├── road/                      # India-only RDD (notebook CELL 4)
│   ├── train/images + labels
│   ├── val/images + labels
│   ├── test/images + labels
│   └── data.yaml
└── crack/                     # optional YOLO boxes (images + labels)
```

## Road (RDD India)

Filter only (no training). Source on Drive (shared `RDD_SPLIT` folder):

https://drive.google.com/drive/folders/16aHH0rPP5-0BSoC3LV_5yj0AT5r46Ed3?usp=sharing

Add a shortcut to My Drive, then Colab can use `/content/drive/MyDrive/RDD_SPLIT`.

**On this PC** (if RDD_SPLIT is downloaded):

```text
python scripts/filter_india_road.py --src "PATH\TO\RDD_SPLIT"
```

**In Colab** (data is on Drive): open `notebooks/filter_india_to_data_road.ipynb`, CPU runtime, run both cells. Output is `data/road/`.

## Crack

Prefer YOLO layout in `data/crack/{train,val}/{images,labels}`.

If that is missing, the notebook converts masks from:

`municipal_ai_system/data/Crack dataset`

(images + masks, including `train/` / `test/`). Untyped cracks become class 0. `noncrack*` files are skipped.

Class IDs stay Flask-compatible: D00 / D10 / D20 / D40.

Weights land in `municipal_ai_system/app/ai_models/best.pt`.
