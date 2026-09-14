import subprocess
import os
import re
from file_control import find_file


def run_git_command(command: str, project_path: str = "") -> str:
    """
    Runs a git command (e.g. 'status', 'pull', 'log -1') inside a project folder.
    Defaults to the Atlas project folder itself if no path is given.
    """
    project_path = project_path.strip() or os.getcwd()
    try:
        args = ["git"] + command.strip().split()
        result = subprocess.run(args, cwd=project_path, capture_output=True, text=True, timeout=20)
        output = (result.stdout or result.stderr).strip()
        if not output:
            return f"Git {command} completed with no output."
        return output[:400]  # обрезаем — длинный git log неудобно озвучивать целиком
    except Exception as e:
        print(f"[Ошибка run_git_command]: {e}")
        return f"Couldn't run git {command}: {e}"


def open_vscode_project(path: str) -> str:
    """Opens a folder in VS Code, searching by name if a full path isn't given."""
    resolved = path
    if not os.path.isdir(resolved):
        found = find_file(path)
        if found and os.path.isdir(found):
            resolved = found
        else:
            return f"Couldn't find a project folder called '{path}'."
    try:
        subprocess.Popen(["code", resolved], shell=True)
        return f"Opening {os.path.basename(resolved)} in VS Code."
    except Exception as e:
        print(f"[Ошибка open_vscode_project]: {e}")
        return "Couldn't open VS Code — is it installed and in PATH?"


def calculate(expression: str) -> str:
    """Safely evaluates a basic math expression, e.g. '12 * (5 + 3)'."""
    # Разрешаем только цифры/операторы — никакого произвольного кода через eval
    if not re.fullmatch(r"[0-9\.\+\-\*\/\(\)\s%]+", expression):
        return "That doesn't look like a valid math expression."
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return f"{expression} equals {result}."
    except Exception as e:
        return f"Couldn't calculate that: {e}"


_UNIT_CONVERSIONS = {
    ("km", "mi"): 0.621371, ("mi", "km"): 1.60934,
    ("kg", "lb"): 2.20462, ("lb", "kg"): 0.453592,
    ("c", "f"): lambda c: c * 9 / 5 + 32,
    ("f", "c"): lambda f: (f - 32) * 5 / 9,
    ("m", "ft"): 3.28084, ("ft", "m"): 0.3048,
}


def convert_units(value: float, from_unit: str, to_unit: str) -> str:
    """Converts a value between common units: km/mi, kg/lb, celsius/fahrenheit, m/ft."""
    key = (from_unit.lower().strip(), to_unit.lower().strip())
    if key not in _UNIT_CONVERSIONS:
        return f"I don't know how to convert {from_unit} to {to_unit}."
    factor = _UNIT_CONVERSIONS[key]
    result = factor(value) if callable(factor) else value * factor
    return f"{value} {from_unit} is {result:.2f} {to_unit}."