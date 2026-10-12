# In-Place Animation Tool for Blender

<p align="center">
  <img src="https://img.shields.io/badge/Blender-3.0%20--%204.5%2B-orange.svg?style=flat-square&logo=blender" alt="Blender Support" />
  <img src="https://img.shields.io/badge/License-GPL--3.0-blue.svg?style=flat-square" alt="License" />
  <img src="https://img.shields.io/badge/Version-1.0.0-green.svg?style=flat-square" alt="Version" />
  <img src="https://img.shields.io/badge/Author-TIJEY%20Studio-9cf.svg?style=flat-square" alt="Author" />
</p>

A lightweight, powerful, and mathematically precise Blender add-on that converts **Root Motion (travelling) animations** into **In-Place animations** with a single click.

Works seamlessly with animations from **Cascadeur**, **Mixamo**, **Motion Capture (mocap)**, custom rigs (**Metarig / Rigify**), and standard game character hierarchies.

---

## 🌟 Why In-Place Animation Tool?

When converting traveling animations into in-place animations, standard approaches (such as simply keying or zeroing the local bone curves) often cause frustrating problems:
* **Foot Sinking / Ground Drift:** Because vertical spine and hip bones in Blender rigs (like Rigify/Metarig) have slight rest angle tilts ($\approx 14.5^\circ$), their local $Z$ and $Y$ axes couple forward travel with vertical height. Zeroing the local curves causes the character's feet to sink 15 cm into the ground or float in mid-air.
* **Manual Cleanup:** Hand-editing dozens of F-curves for every clip takes hours of tedious work.

**In-Place Animation Tool** solves this mathematically:
It computes motion in **True 3D World Space** ($T_{\text{world}} = R_{\text{world}} \cdot \text{loc}$), neutralizes horizontal forward and lateral travel, and **preserves 100% of the character's original world height across every frame**. Your character's feet stay perfectly planted on the floor, while crouching, jumping, and sitting down are preserved with zero distortion!

---

## ⚡ Key Features

- **⚡ 1-Click Conversion:** Just select your armature and click `Make In-Place (1-Click)`.
- **🎯 Smart Auto-Detection:** Automatically scans action curves and detects the true root-driving bone (`spine`, `hips`, `pelvis`, `root`) or Armature object.
- **🦶 Zero Floor Sinking:** World-matrix 3D projection eliminates bone tilt cross-coupling. Feet stay on the floor across the entire timeline.
- **🎮 Game Engine Ready:** Choose between `Origin (0, 0, 0)` for game engine controllers (Unreal Engine / Unity) or `First Frame Position` to retain initial staging.
- **🏃 Natural Sway Preservation (`Remove Drift` Mode):** Cancels net travel while keeping subtle hip swaying and weight-shifting oscillations intact.
- **🦴 Root Bone Extraction:** Automatically generates a ground-level `root` bone and transfers forward movement onto it for Unreal/Unity Root Motion pipelines.
- **🛡️ 100% Non-Destructive:** Automatically creates an action backup before applying changes. Restore the original animation anytime with 1 click.
- **🌐 Broad Version Support:** Compatible with **Blender 3.0, 3.3 LTS, 3.6 LTS, 4.0, 4.1, 4.2 LTS, 4.4, and 4.5+**.

---

## 📥 Installation

### Method A: Blender 4.2+ (Extensions Platform)
1. Download the latest `inplace_anim_tool-1.0.0.zip` from [Releases](https://github.com/tijeyoff/inplace-anim-tool/releases).
2. In Blender, go to **Edit > Preferences > Get Extensions**.
3. Click the menu dropdown (top-right) and select **Install from Disk...**.
4. Select the `.zip` file. Done!

### Method B: Blender 3.0 – 4.1 (Standard Add-on)
1. Download `inplace_anim_tool-1.0.0.zip` from [Releases](https://github.com/tijeyoff/inplace-anim-tool/releases).
2. Go to **Edit > Preferences > Add-ons**.
3. Click **Install...**, select the `.zip` file, and enable the checkmark next to **Animation: In-Place Animation Tool**.

---

## 📖 How to Use

1. Open the **3D Viewport** and press **`N`** to reveal the sidebar.
2. Click on the **`In-Place`** tab.
3. Select your animated armature.
4. The tool automatically analyzes the animation and displays motion metrics ($\Delta X$, $\Delta Y$, $\Delta Z$).
5. Click **`⚡ Make In-Place (1-Click)`**.
6. Press Spacebar to play your in-place animation!

---

## ⚙️ Settings Reference

| Setting | Options | Description |
|---|---|---|
| **Target Scope** | `Auto`, `Target Bone`, `Armature Object`, `Both` | Defines what entity to neutralize. `Auto` intelligently selects the moving bone. |
| **Freeze X (Side)** | `True` / `False` | Locks side-to-side lateral displacement. |
| **Freeze Y (Fwd)** | `True` / `False` | Locks forward and backward travel. |
| **Freeze Z (Height)** | `True` / `False` | **Leave Unchecked** to preserve crouching, sitting, jumping, and floor contact. Check only if you need a strictly planar animation. |
| **Freeze Yaw (Rot Z)** | `True` / `False` | Keeps the character facing a constant direction. |
| **Mode** | `Absolute Lock (Freeze)` / `Remove Drift` | `Freeze` locks the axis to an anchor. `Remove Drift` removes linear forward velocity while preserving hip sway. |
| **Anchor** | `Origin (0, 0, 0)` / `First Frame Position` | `Origin` aligns the character to world (0,0,0) (ideal for game engines). `First Frame` anchors to starting scene coordinates. |
| **Backup Action** | `True` / `False` | Automatically duplicates the action with suffix `_Original_Backup` before editing. |

---

## 🎮 Game Engine Tools: Root Bone Extraction

For game engines (Unreal Engine 5 Character Movement Component, Unity Mecanim):
1. In the **Game Engine Tools** subpanel, click **`Extract Motion to Root Bone`**.
2. The tool creates a ground-level `root` bone at $(0, 0, 0)$ and transfers all horizontal velocity to it.
3. The pelvis/hips bone stays in-place horizontally while retaining full vertical movement and rotations.
4. Export your FBX with Root Motion enabled!

---

## 📄 License

This project is licensed under the **GNU General Public License v3.0 (GPL-3.0)** — see the [LICENSE](LICENSE) file for details.

---

## 👤 Author & Support

Developed with ❤️ by **TIJEY Studio**
- **GitHub:** [@tijeyoff](https://github.com/tijeyoff)
- **Official Extensions Page:** [Blender Extensions](https://extensions.blender.org/add-ons/inplace_anim_tool/)

If you find this tool helpful, please give this repository a ⭐ on GitHub!
