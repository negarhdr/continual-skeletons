import subprocess
import os
from pathlib import Path
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

ROOT_PATH = Path(os.getenv("ROOT_PATH", default=""))
LOGS_PATH = Path(os.getenv("LOGS_PATH", default="logs"))
DATASETS_PATH = Path(os.getenv("DATASETS_PATH", default="datasets"))

GPUS = "0,"
LOGGING_BACKEND = "wandb"
DS_NAME = "ntu"
DS_PATH = DATASETS_PATH / DS_NAME
DS_SUBSET = "xview"
MODALITY = "joint"


subprocess.call(
    [
        "python3",
        "models/st_gcn/st_gcn.py",
        "--id",
        "pretrained",
        "--gpus",
        GPUS,
        "--test",
        "--profile_model",
        "--batch_size",
        "128",
        "--num_workers",
        "8",
        "--dataset_normalization",
        "0",
        "--dataset_name",
        DS_NAME,
        "--dataset_classes",
        str(DS_PATH / "classes.yaml"),
        "--dataset_train_data",
        str(DS_PATH / DS_SUBSET / f"train_data_{MODALITY}.npy"),
        "--dataset_val_data",
        str(DS_PATH / DS_SUBSET / f"val_data_{MODALITY}.npy"),
        "--dataset_test_data",
        str(DS_PATH / DS_SUBSET / f"val_data_{MODALITY}.npy"),
        "--dataset_train_labels",
        str(DS_PATH / DS_SUBSET / "train_label.pkl"),
        "--dataset_val_labels",
        str(DS_PATH / DS_SUBSET / "val_label.pkl"),
        "--dataset_test_labels",
        str(DS_PATH / DS_SUBSET / "val_label.pkl"),
        "--finetune_from_weights",
        str(
            ROOT_PATH
            / "pretrained_models"
            / "stgcn"
            / "nturgbd60_cv"
            / "ntu_cv_stgcn_joint-49-29400.pt"
        ),
        "--logging_backend",
        "wandb",
        # "--help",
    ]
)