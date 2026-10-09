"""
Дела с файлами по плану: «разбери загрузки», «собери все эссе в одну папку» → план → «да» → отчёт.
Подтверждение проверяет код, а не модель: нужна НОВАЯ реплика пользователя со словом согласия.
"""
from core import file_drop, file_plans
from core.skills import skill

try:
    import tool_router
    tool_router.TRIGGERS["file_tasks"] = (
        "разбери", "разобрать", "наведи порядок", "порядок в", "рассортируй", "отсортируй", "собери", "сложи",
        "загрузк", "downloads", "верни как было", "отмени план", "выполни план", "подтверждаю", "да, выполни",
        "tidy", "sort my", "organize", "collect all", "undo",
        "пришли", "скинь", "отправь на телефон", "на телефон", "send me", "send to my phone",
    )
except Exception:
    pass


def _question() -> str:
    try:
        from brain import state
        return state.turn.get("question") or ""
    except Exception:
        return ""


@skill("file_tasks",
       description="Makes a PLAN (does not move anything yet) to tidy a folder on the computer: sorts its files into "
                   "subfolders PDF, Документы, Таблицы, Картинки, Видео, Архивы, Установщики… Files newer than an hour "
                   "are left alone, nothing is deleted. Then read the plan to the user and ask to confirm.",
       params={"folder": "downloads | desktop | documents | pictures, or a full path"})
def plan_tidy_folder(folder: str = "downloads") -> str:
    return file_plans.describe(file_plans.plan_tidy(folder, question=_question()))


@skill("file_tasks",
       description="Makes a PLAN to collect files whose names contain the given words (e.g. 'эссе', 'SAT') into one "
                   "folder in Documents by COPYING them (originals stay). Then read the plan and ask to confirm.",
       params={"query": "words that must be in the file names", "folder_name": "name of the new folder, optional"})
def plan_collect_files(query: str, folder_name: str = "") -> str:
    return file_plans.describe(file_plans.plan_collect(query, folder_name, question=_question()))


@skill("file_tasks",
       description="Runs the file plan the user has just confirmed ('yes', 'да, выполняй'). Refuses if the user did "
                   "not say yes in their latest message.")
def confirm_file_plan() -> str:
    return file_plans.confirm(_question())


@skill("file_tasks", description="Cancels the file plan that is waiting for confirmation ('no', 'отмена').")
def cancel_file_plan() -> str:
    return file_plans.cancel()


@skill("file_tasks", description="Undoes the last file plan that was run: puts moved files back, removes copies Atlas "
                                 "made ('undo', 'верни как было').")
def undo_file_plan() -> str:
    return file_plans.undo()


@skill("file_tasks",
       description="Sends a file from this computer to the user's phone: 'пришли мне на телефон эссе про климат', "
                   "'скинь SAT_practice.pdf'. Finds it by name, by what the user worked on, or by its content. "
                   "query: the file name or words about it; a number picks a result of the last file search.",
       params={"query": "file name, words about the file, or a number from the last search"})
def send_file_to_phone(query: str) -> str:
    return file_drop.send(query)
