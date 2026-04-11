# Reply Directly – 智能群聊增强插件（AstrBot）

> 让机器人在群聊里像真人一样“会聊天”，支持**沉浸式连续对话**与**主动插话**两大核心能力。  
> 项目地址：[GitHub](https://github.com/qa296/astrbot_plugin_reply_directly)

---

</div>

<div align="center">

[![Moe Counter](https://count.getloli.com/get/@astrbot_plugin_reply_directly)](https://github.com/qa296/astrbot_plugin_reply_directly/)

</div>

---

## 功能亮点

| 能力 | 描述 | 示例 |
| --- | --- | --- |
| **沉浸式对话**<br>Immersive Chat | 机器人回答后，**无需 @ 机器人**，用户可直接追问，机器人会基于完整上下文继续回复。 | 你：帮我规划上午复习数学，下午学编程。<br>机器人：好的……<br>你：下午学 Python 具体点？<br>机器人：（直接继续回答） |
| **主动插话**<br>Proactive Interjection | 机器人发言后，监听后续聊天 8 秒；若 LLM 判断话题相关，则**主动发表意见**。 | 机器人：今天天气真好。<br>A：去打球？<br>B：去哪儿打？<br>机器人（8 秒后）：我知道新开的球场，需要地址吗？ |

---

## 更新说明

>  v1.4.0 更改了功能**沉浸式对话**不再是由llm主动触发函数。  
>  v1.4.2 修复无法正确配置人格的问题。  
>  v1.4.4 1.把沉浸式对话内容写入对话库。2.为指令前缀做出了检测（指令不会触发llm）  
>  v1.4.5 1.修复了无法正常触发沉浸式对话的bug。2.修改了提示词使llm更好的判断是否回复。
>  v1.4.5 1.加固了llm的提示词。2.使功能更多。

---

## 快速安装

```bash
# 1. 克隆仓库
git clone https://github.com/qa296/astrbot_plugin_reply_directly.git

# 2. 放入插件目录
cp -r astrbot_plugin_reply_directly  YOUR_ASTRBOT_DIR/data/plugins/

# 3. 重启 AstrBot
./restart.sh   # 或你喜欢的启动方式
```

---

## 配置指南

在 **AstrBot WebUI → 插件管理 → Reply Directly** 中可视化调整：

| 配置项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enable_plugin` | bool | `true` | 总开关，关闭后所有功能失效。 |
| `enable_immersive_chat` | bool | `true` | 是否允许沉浸式连续对话。 |
| `enable_proactive_reply` | bool | `true` | 是否允许机器人主动插话。 |
| `proactive_reply_delay` | int | 8 | 机器人发言后等待多少秒再检查插话（5–30 秒体验最佳）。 |
| `immersive_reply_timeout` | int | 120 | 沉浸式会话有效期（秒）。 |

---


喜欢本项目？欢迎点个 ⭐！  
有任何问题或建议，请前往 [GitHub Issues](https://github.com/qa296/astrbot_plugin_reply_directly/issues) 讨论。

## QQ群
因为作者并不经常看issue，所以可以通过QQ群来提醒我！
群号：1098607348
