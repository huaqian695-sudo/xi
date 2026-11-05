"""带图出话。

跟 chat.reply 同一套料，差别在两处：
1. 多一个"图片指令"常量块（先看图说话 / 图是分享不是证据 / 看不清直说）
2. user 里挂图片引用（M1 只挂路径，M3 接真模型时换成像素）

所以这里没有代码差异——差异全在 recipe.yml 里。
"""

from __future__ import annotations

from scenes.base import RecipeScene


class ChatImage(RecipeScene):
    name = "chat.image"
