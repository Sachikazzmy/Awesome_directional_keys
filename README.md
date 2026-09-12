# Awesome Directional Keys

> A keyboard navigation tool for macOS, designed to reduce mouse dependency in multi-window learning and coding workflows.

**Awesome Directional Keys** 是一个面向 macOS 的键盘效率工具。

它的目标很简单：

**尽可能减少在学习、编程等多窗口场景下对鼠标的依赖。**

在日常学习和编程过程中，经常需要在浏览器、VS Code、终端以及多个窗口之间切换，同时还需要频繁滚动页面、翻页和切换编辑区域。

这个项目尝试使用一套统一的键盘操作方式完成这些动作。

---

## ✨ Features

当前项目处于早期开发阶段，目前已经实现：

* 全局键盘快捷键监听
* 键盘控制页面滚动 / 翻页
* 键盘控制窗口焦点切换
* macOS 窗口控制
* 后台持续运行
* 支持通过 macOS `launchd` 自动启动

### Current Keybindings

| Shortcut    | Action       |
| ----------- | ------------ |
| `Shift + A` | 切换到左侧窗口 / 区域 |
| `Shift + D` | 切换到右侧窗口 / 区域 |
| `Shift + W` | 向上翻页 / 滚动    |
| `Shift + S` | 向下翻页 / 滚动    |

> 快捷键和具体行为仍在开发中，后续可能会调整。

---

## 🎯 Motivation

这个项目最初来自一个非常具体的使用场景：

```text
┌──────────────┐
│   ChatGPT    │
│              │
│              │
└──────────────┘

┌──────────────────────────────┐
│ Browser       │   VS Code    │
│               │              │
│ Tutorial      │   Code       │
│               │              │
└──────────────────────────────┘
```

当学习编程时，我经常需要：

1. 阅读浏览器中的教程
2. 在 VS Code 中编写代码
3. 在终端运行程序
4. 在不同窗口之间切换
5. 不断上下滚动和翻页

这些操作本身并不复杂，但频繁将手从键盘移动到鼠标会打断操作流程。

因此，我希望尝试一种更加简单的方式：

> **让键盘本身承担更多的导航工作。**

---

## 🏗️ Architecture

项目目前采用简单的分层结构：

```text
src/
├── keyboards/
│   └── 负责键盘监听与快捷键触发
│
├── actions/
│   └── 定义具体要执行的操作
│
├── macos/
│   └── 封装 macOS 系统能力
│
├── config/
│   └── 配置相关内容
│
└── main.py
    └── 程序入口与模块组装
```

整体调用关系：

```text
Keyboard Shortcut
        │
        ▼
   keyboards
        │
        ▼
     actions
        │
        ▼
      macos
        │
        ▼
      macOS
```

这样的结构主要是为了将：

* **快捷键是什么**
* **需要执行什么操作**
* **macOS 如何实现这个操作**

三个问题分开。

---

## 🛠️ Tech Stack

* **Python**
* **pynput** — 全局键盘监听与输入控制
* **PyObjC** — Python 与 macOS API 的桥接
* **Quartz / ApplicationServices** — macOS 输入与窗口相关能力
* **PyYAML** — 配置管理

依赖版本见 [`requirements.txt`](./requirements.txt)。

---

## 🚀 Getting Started

### Requirements

* macOS
* Python 3
* Accessibility permission

由于项目需要监听全局键盘输入并控制窗口，需要授予相应的 macOS 权限。

---

### 1. Clone

```bash
git clone https://github.com/Sachikazzmy/Awesome_directional_keys.git
cd Awesome_directional_keys
```

### 2. Create virtual environment

```bash
python3 -m venv .venv
```

### 3. Activate environment

```bash
source .venv/bin/activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Run

```bash
python src/main.py
```

程序启动后会在后台监听快捷键。

---

## 🔐 macOS Permissions

第一次运行时，如果相关功能无法正常工作，请检查：

**System Settings → Privacy & Security**

根据实际功能开启：

* Accessibility
* Input Monitoring

如果使用虚拟环境运行，需要注意 macOS 权限可能需要授予实际运行 Python 程序的路径。

---

## ⚙️ Run in Background

项目可以通过 macOS `launchd` 作为 LaunchAgent 持续运行。

这样可以实现：

```text
登录 macOS
    ↓
launchd
    ↓
Awesome Directional Keys
    ↓
后台持续运行
```

后续将提供更加完善的安装与自动启动方式。

---

## 🗺️ Roadmap

项目目前仍处于早期阶段。

计划逐步实现：

* [x] 全局快捷键监听
* [x] 基础页面滚动 / 翻页
* [x] 基础窗口切换
* [x] macOS 后台运行
* [ ] 更完善的快捷键配置
* [ ] VS Code 编辑器 / 终端区域切换
* [ ] 更多窗口导航能力
* [ ] 配置文件
* [ ] 更完善的 macOS 安装方式
* [ ] 打包为独立 macOS 应用
* [ ] 自动化测试
* [ ] GitHub Actions CI

---

## 📌 Project Status

**Early Development / MVP**

这是一个正在持续开发中的个人开源项目。

整个项目也会作为我学习 Python、macOS API 以及完整软件开发流程的实践项目。

---

## 📄 License

License TBD.
