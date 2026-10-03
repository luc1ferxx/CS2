# 第三方声明

本文件列出本仓库借用的第三方作品及其许可。雷达图的来源与致谢另见 [frontend/public/maps/ATTRIBUTION.md](frontend/public/maps/ATTRIBUTION.md)。

## cs2-sandbox（MIT）

- 来源：[bugkingZHT/cs2-sandbox](https://github.com/bugkingZHT/cs2-sandbox) 的 `frontend/src/components/ReplayPlayer/KeyboardOverlay.vue`
- 借用的内容：复盘页实时按键显示的视觉设计，包括布局、尺寸、颜色、模糊背景、圆角按键、按下时的蓝色高亮与 0.3 秒脉冲，以及带"左键 / 右键"标注的 SVG 鼠标。
- 本仓库中的对应文件：`frontend/components/replay/KeyboardOverlay.tsx` 和 `frontend/app/product.css` 中的 `/* === S16 KEYS === */` 一节。它们用 React/TSX 和 CSS 重新实现，没有引入 Vue 代码；按键数据来自本项目自己的解析器（replay contract v3 的 `inputs`），与 cs2-sandbox 的数据管线无关。

许可原文：

```text
MIT License

Copyright (c) 2026 huN7er

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
