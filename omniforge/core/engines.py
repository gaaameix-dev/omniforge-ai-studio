"""Game-engine project scaffolding (Unity, Unreal, Godot).

These adapters write a *text* project skeleton - scripts, project files and config -
that you can open in the engine. They do not compile, build or launch anything, and
they do not generate 3D assets: that needs the editor or an asset pipeline.
"""
from __future__ import annotations

import re
from typing import Any

ENGINES = ("godot", "unity", "unreal")


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", name or "Game").strip("_") or "Game"


def scaffold(engine: str, *, name: str = "MyGame", genre: str = "", notes: str = "") -> dict[str, str]:
    """Return {path: file contents} for a minimal, openable project."""
    engine = (engine or "").lower()
    project = _slug(name)
    if engine == "godot":
        return {
            "project.godot": (
                "; Engine configuration file.\n"
                "config_version=5\n\n"
                "[application]\n\n"
                f'config/name="{name}"\n'
                'run/main_scene="res://scenes/main.tscn"\n\n'
                "[rendering]\n\nrenderer/rendering_method=\"forward_plus\"\n"
            ),
            "scenes/main.tscn": (
                "[gd_scene load_steps=2 format=3]\n\n"
                "[ext_resource type=\"Script\" path=\"res://scripts/player.gd\" id=\"1\"]\n\n"
                "[node name=\"Main\" type=\"Node2D\"]\n\n"
                "[node name=\"Player\" type=\"CharacterBody2D\" parent=\".\"]\n"
                "script = ExtResource(\"1\")\n"
            ),
            "scripts/player.gd": (
                "extends CharacterBody2D\n\n"
                "const SPEED := 220.0\n\n"
                "func _physics_process(_delta: float) -> void:\n"
                "    var direction := Input.get_vector(\"ui_left\", \"ui_right\", \"ui_up\", \"ui_down\")\n"
                "    velocity = direction * SPEED\n"
                "    move_and_slide()\n"
            ),
            "README.md": f"# {name}\n\nGodot project skeleton.\n\nGenre: {genre or 'n/a'}\n\n{notes}\n",
        }
    if engine == "unity":
        return {
            "ProjectSettings/ProjectVersion.txt": "m_EditorVersion: 2022.3.20f1\n",
            "Assets/Scripts/PlayerController.cs": (
                "using UnityEngine;\n\n"
                "public class PlayerController : MonoBehaviour\n{\n"
                "    public float speed = 5f;\n\n"
                "    void Update()\n    {\n"
                "        float h = Input.GetAxis(\"Horizontal\");\n"
                "        float v = Input.GetAxis(\"Vertical\");\n"
                "        transform.Translate(new Vector3(h, 0f, v) * speed * Time.deltaTime);\n"
                "    }\n}\n"
            ),
            "Assets/Scripts/GameManager.cs": (
                "using UnityEngine;\n\n"
                "public class GameManager : MonoBehaviour\n{\n"
                "    public static GameManager Instance { get; private set; }\n\n"
                "    void Awake()\n    {\n"
                "        if (Instance != null && Instance != this) { Destroy(gameObject); return; }\n"
                "        Instance = this;\n    }\n}\n"
            ),
            "README.md": f"# {name}\n\nUnity project skeleton (scripts + settings).\n\n"
                         f"Genre: {genre or 'n/a'}\n\n{notes}\n",
        }
    if engine == "unreal":
        return {
            f"{project}.uproject": (
                '{\n  "FileVersion": 3,\n'
                f'  "EngineAssociation": "5.3",\n  "Category": "",\n  "Description": "{notes}",\n'
                '  "Modules": [\n    {\n'
                f'      "Name": "{project}",\n      "Type": "Runtime",\n'
                '      "LoadingPhase": "Default"\n    }\n  ]\n}\n'
            ),
            f"Source/{project}/{project}.Build.cs": (
                "using UnrealBuildTool;\n\n"
                f"public class {project} : ModuleRules\n{{\n"
                f"    public {project}(ReadOnlyTargetRules Target) : base(Target)\n    {{\n"
                '        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;\n'
                '        PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", '
                '"Engine", "InputCore" });\n'
                "    }\n}\n"
            ),
            f"Source/{project}/{project}GameMode.cpp": (
                f'#include "CoreMinimal.h"\n#include "GameFramework/GameModeBase.h"\n'
                f'#include "{project}GameMode.h"\n'
            ),
            "Config/DefaultEngine.ini": (
                "[/Script/EngineSettings.GameMapsSettings]\n"
                "GameDefaultMap=/Game/Maps/Main.Main\nEditorStartupMap=/Game/Maps/Main.Main\n"
            ),
            "README.md": f"# {name}\n\nUnreal project skeleton (module + config).\n\n"
                         f"Genre: {genre or 'n/a'}\n\n{notes}\n",
        }
    raise ValueError(f"unknown engine '{engine}'; choose one of {', '.join(ENGINES)}")


def summarise(files: dict[str, str]) -> dict[str, Any]:
    return {
        "files": sorted(files),
        "count": len(files),
        "bytes": sum(len(text.encode()) for text in files.values()),
        "note": "text skeleton only: open it in the engine; nothing is compiled here",
    }
