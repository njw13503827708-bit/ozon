# Image generator contract

Use only the current product's real input images as references. Preserve product structure, color, SKU differences, and accessory count. `edit_real_image` edits a real reference; `compose_from_real_images` keeps product pixels from real references. A SKU reference below 600 pixels needs a higher-resolution image of the same product to preserve detail.

Use at most one built-in image generation/edit call per slot invocation. If output or technical checks fail, write a FAIL receipt with the reason. The parent batch runner owns targeted retries. Do not ask the user during an authorized unattended batch.
