#!/usr/bin/env python3
"""Example: report live training status to the Scopos TUI.

Run this in one terminal::

    python examples/report_to_scopos.py

and watch it in another with zen mode focused on your user::

    scopos -u "$USER" --zen

You'll see this process appear with ``stage``, ``loss`` and an ``epoch``
progress bar updating live, then disappear when the script exits.
"""

import sys
sys.path.insert(0, "/data/tianzhen/my_packages/scopos/src")

import random
import time
# from tqdm import tqdm

import scopos

TOTAL_EPOCHS = 50


def main():
    # An indeterminate (animated) bar while we "load data".
    scopos.report(stage="warmup", epoch=scopos.progress(label="loading data"))
    time.sleep(3)

    loss = 2.5
    for epoch in range(TOTAL_EPOCHS):
        loss *= random.uniform(0.92, 0.99)
        print(f"Epoch {epoch+1}/{TOTAL_EPOCHS}, loss={loss:.4f}")
        scopos.report(
            stage="train",
            epoch=scopos.progress(epoch + 1, TOTAL_EPOCHS),  # e.g. 12/50
            loss=f"{loss:.4f}",
        )
        time.sleep(0.5)

    scopos.report(stage="done", acc=f"{random.uniform(90, 99):.1f}%")
    time.sleep(2)
    # scopos.clear() runs automatically at exit, but you can call it explicitly.


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
