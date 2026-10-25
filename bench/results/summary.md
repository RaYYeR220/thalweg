| Build | s per clip (median of 5) | clips / hour | $ / 1000 gaugings |
|---|---|---|---|
| Graviton4 `c8g.large`, COOL (OpenCV 5.1.0-dev, KleidiCV 0.7, SVE baseline) | 38.9 | 93 | 0.97 |
| Graviton4 `c8g.large`, pip `opencv-python-headless==5.0.0.93` (KleidiCV 26.03) | 37.7 | 95 | 0.84 |
| x86 `c7i.large`, pip `opencv-python-headless==5.0.0.93` | 39.7 | 91 | 0.98 |

| Operation (ms, median) | COOL | pip, Graviton4 | pip, x86 |
|---|---|---|---|
| `resize_area_4k_to_1080p` | 17.696 | 17.718 | 35.519 |
| `dis_flow_600` | 16.514 | 17.451 | 30.554 |
| `matchTemplate_31_in_55_x100` | 6.971 | 6.298 | 4.217 |
| `remap_ortho_600x600` | 0.414 | 0.417 | 0.684 |
| `warpPerspective_1080p` | 4.039 | 3.271 | 3.975 |
| `phaseCorrelate_600` | 7.118 | 8.171 | 3.846 |
| `cvtColor_bgr2gray_1080p` | 0.34 | 0.324 | 0.621 |

The workload is one complete gauging of an 8-second clip (VGC video 1) by the deterministic agent: stabilization, orthorectification, diagnostics, LSPIV, STIV and discharge. All three builds return the same decision, and the two Arm builds return the same discharge to four decimals. The x86 build differs in the third decimal.

**What we found.** On this workload, COOL is not faster than the stock OpenCV 5 wheel on the same Graviton4 instance (38.9 s vs 37.7 s). The pip wheel for aarch64 already ships KleidiCV, in a newer version than the COOL build, and our time goes into ensemble correlation, ALIKED/LightGlue inference and numpy, not the colour, resize and contour kernels COOL targets. Graviton4 itself is the saving: the same gauging costs about 15% less than on x86 (c7i). The production worker runs COOL; `bench/results/` records which `cv2` ran, its HAL, the instance type and the AMI product code for each build. The obvious next step is our own kernels: LSPIV and STIV are Python loops around OpenCV calls and are the two largest stages.
