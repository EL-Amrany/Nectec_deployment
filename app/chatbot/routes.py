# app/chatbot/routes.py

from flask import request, jsonify, current_app
from flask_login import login_required, current_user
from . import chatbot
from ..models import db, Progress, Module

import re
import requests
from markupsafe import Markup
from markdown import markdown

# LangChain (community) + RAG bits
from langchain_community.chat_models import ChatOllama
from langchain_community.document_loaders import DirectoryLoader
from langchain.indexes import VectorstoreIndexCreator
from langchain_community.embeddings import HuggingFaceEmbeddings

# -----------------------------
# Ollama / Model configuration
# -----------------------------
OLLAMA_BASE_URL = "http://localhost:11434"

# We’ll pick the first one that exists on your machine.
PREFERRED_CHAT_MODELS = [
    "mistral:latest",
    "llama3.1:latest",
    "neural-chat:latest",
    "granite3.2:latest",
]

def _list_ollama_models(base_url: str) -> set:
    """
    Ask the local Ollama daemon what models are installed.
    Returns a set of tag strings like {'mistral:latest', 'llama3.1:latest', ...}
    """
    try:
        r = requests.get(f"{base_url}/api/tags", timeout=3)
        r.raise_for_status()
        data = r.json()
        return {m.get("name") for m in data.get("models", [])}
    except Exception:
        return set()

def _pick_available_model(preferred: list[str], available: set[str]) -> str:
    for m in preferred:
        if m in available:
            return m
    raise RuntimeError(
        f"No preferred models are available. Installed: {sorted(available)}. "
        f"Pull one (e.g. `ollama pull mistral:latest`) or change PREFERRED_CHAT_MODELS."
    )

_available = _list_ollama_models(OLLAMA_BASE_URL)
if not _available:
    # Most common cause: Ollama daemon not running.
    raise RuntimeError(
        "Cannot reach Ollama at http://localhost:11434.\n"
        "Start it with `ollama serve` (or open the Ollama app)."
    )

_SELECTED_MODEL = _pick_available_model(PREFERRED_CHAT_MODELS, _available)

# -----------------------------
# Instantiate LLM + Embeddings
# -----------------------------
llm = ChatOllama(
    model=_SELECTED_MODEL,
    base_url=OLLAMA_BASE_URL,
    temperature=0.2,
)

# Use a tiny HF model for embeddings (fast, solid for RAG)
embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

# Build the vector index from your local docs
loader = DirectoryLoader("data/")
index_creator = VectorstoreIndexCreator(embedding=embeddings)
index0 = index_creator.from_loaders([loader])

# -----------------------------
# Your domain logic (unchanged)
# -----------------------------
def get_learning_objective(role, module_key, current_level):
    objectives = {
        "ai_specialist": {
            "A1": {"Apprentice": "remember", "Practitioner": "understand", "Competent": "apply"},
            "A2": {"Apprentice": "remember", "Practitioner": "apply", "Competent": "analyze"},
            "A3": {"Practitioner": "understand", "Competent": "apply"},
            "A4": {}, "A5": {},
            "B1": {"Apprentice": "apply", "Practitioner": "apply", "Competent": "evaluate"},
            "B2": {"Apprentice": "understand", "Practitioner": "apply", "Competent": "analyze"},
            "B3": {"Apprentice": "remember", "Practitioner": "understand", "Competent": "understand"},
            "B4": {}, "B5": {}, "B6": {},
            "C1": {"Apprentice": "understand", "Practitioner": "understand", "Competent": "apply"},
            "D1": {"Apprentice": "understand", "Practitioner": "understand", "Competent": "apply"},
            "D2": {"Apprentice": "remember", "Practitioner": "understand", "Competent": "apply"},
            "D3": {"Apprentice": "apply", "Practitioner": "apply", "Competent": "apply"},
            "E1": {"Apprentice": "understand", "Practitioner": "apply", "Competent": "evaluate"},
            "E2": {"Apprentice": "understand", "Practitioner": "apply", "Competent": "create"},
            "E3": {},
        },
        "comp_chem_specialist": {
            "A1": {"Apprentice": "remember", "Practitioner": "understand", "Competent": "apply"},
            "A2": {"Apprentice": "remember", "Practitioner": "apply", "Competent": "analyze"},
            "A3": {"Practitioner": "understand", "Competent": "apply"},
            "A4": {}, "A5": {},
            "B1": {"Apprentice": "apply", "Practitioner": "apply", "Competent": "evaluate"},
            "B2": {"Competent": "apply"},
            "B3": {"Apprentice": "remember", "Practitioner": "understand", "Competent": "apply"},
            "B4": {}, "B5": {}, "B6": {},
            "C1": {"Apprentice": "remember", "Practitioner": "understand", "Competent": "apply"},
            "D1": {"Apprentice": "understand", "Practitioner": "understand", "Competent": "apply"},
            "D2": {"Apprentice": "remember", "Practitioner": "understand", "Competent": "apply"},
            "D3": {"Apprentice": "apply", "Practitioner": "apply", "Competent": "apply"},
            "E1": {"Apprentice": "understand", "Practitioner": "apply", "Competent": "evaluate"},
            "E2": {"Apprentice": "understand", "Practitioner": "apply", "Competent": "create"},
            "E3": {},
        },
    }
    return objectives.get(role, {}).get(module_key, {}).get(current_level, "remember")

@chatbot.route('/message', methods=['POST'])
@login_required
def message():
    data = request.json
    user_message = data.get('message', '').strip()
    module_id = data.get('module_id')
    progress = Progress.query.filter_by(user_id=current_user.id, module_id=module_id).first()
    module = Module.query.get(module_id)
    module_desc = module.description or ""

    greetings = ['hello', 'hi', 'hey', 'start', 'good morning', 'good afternoon', 'good evening']
    first_interaction = not progress or (progress and not progress.quiz_in_progress and not progress.quiz_passed and not getattr(progress, "awaiting_quiz_confirmation", False))
    is_greeting = any(user_message.lower().startswith(g) for g in greetings) or user_message.strip() == ""

    role = current_user.role
    level = current_user.current_level
    skill = get_learning_objective(role, module.key, level)

    if not progress:
        progress = Progress(user_id=current_user.id, module_id=module_id, status='in_progress')
        db.session.add(progress)
        db.session.commit()

    # 1) greet / first time
    if first_interaction or is_greeting:
        lesson_prompt, _ = build_lesson_and_quiz_prompts(skill, module.name, module_desc=module_desc)
        try:
            lesson = index0.query(lesson_prompt, llm=llm)
        except Exception as e:
            # If RAG fails (e.g. embeddings/indexing), fall back to direct generation
            lesson = f"Based on '{module.name}', here’s an overview:\n\n" + _llm_simple(llm, lesson_prompt)
        progress.awaiting_quiz_confirmation = True
        progress.quiz_in_progress = False
        progress.quiz_passed = False
        db.session.commit()
        reply = (
            f"👋 Hello! Welcome to the **{module.name}** module.\n\n"
            f"{lesson}\n\n"
            "Are you ready for a multiple-choice question? (Reply 'yes' to continue, or ask if you need more explanation.)"
        )
        return jsonify({'reply': Markup(markdown(reply))})

    # 2) awaiting quiz confirmation
    if getattr(progress, "awaiting_quiz_confirmation", False) and not progress.quiz_in_progress:
        if user_message.lower() in ['yes', 'ready', 'ok', 'yep', 'go', 'sure']:
            _, quiz_prompt = build_lesson_and_quiz_prompts(skill, module.name, module_desc=module_desc)
            try:
                quiz = index0.query(quiz_prompt, llm=llm)
            except Exception:
                quiz = _llm_simple(llm, quiz_prompt)
            answer_match = re.search(r'(?i)Answer\s*[:\-]?\s*([A-D])', quiz)
            quiz_answer = answer_match.group(1).upper() if answer_match else "A"

            progress.quiz_in_progress = True
            progress.awaiting_quiz_confirmation = False
            progress.current_quiz_question = quiz
            progress.quiz_answer = quiz_answer
            progress.current_skill = skill
            progress.last_wrong_attempt = 0
            db.session.commit()

            quiz_text = quiz.split('Answer')[0].strip()
            reply = (
                "**Quiz:**\n"
                f"{quiz_text}\n\n"
                "Please answer with A, B, C, or D."
            )
            return jsonify({'reply': Markup(markdown(reply))})
        else:
            user_question_prompt = (
                f"You are an expert tutor for HPC. The learner said: '{user_message}'. "
                f"Provide a helpful, concise explanation about '{module.name}'.\n\n"
                f"After your answer, say: 'Are you ready for a multiple-choice question? (Reply 'yes' to continue, or ask if you need more explanation.)'"
            )
            try:
                explanation = index0.query(user_question_prompt, llm=llm)
            except Exception:
                explanation = _llm_simple(llm, user_question_prompt)
            progress.awaiting_quiz_confirmation = True
            db.session.commit()
            return jsonify({'reply': Markup(markdown(explanation))})

    # 3) quiz mode
    if progress.quiz_in_progress:
        user_answer = user_message.strip().upper()

        # (Your evaluation block is unchanged)
        if user_answer == progress.quiz_answer:
            progress.quiz_in_progress = False
            progress.quiz_passed = True
            db.session.commit()
            reply = (
                "✅ Correct! Great job—you have mastered this concept. "
                "You can now mark this module as complete."
            )
            return jsonify({'reply': Markup(markdown(reply)), 'quiz_passed': True})
        else:
            progress.last_wrong_attempt += 1
            db.session.commit()
            lesson_prompt, quiz_prompt = build_lesson_and_quiz_prompts(
                progress.current_skill, module.name, module_desc=module_desc, previous_wrong=progress.current_quiz_question
            )
            try:
                improved = index0.query(lesson_prompt, llm=llm)
            except Exception:
                improved = _llm_simple(llm, lesson_prompt)

            quiz_text = progress.current_quiz_question.split('Answer')[0].strip()
            reply = (
                f"❌ That's not correct. Here's a clearer explanation:\n\n"
                f"{improved}\n\n"
                "Let's try the same question again:\n\n"
                f"{quiz_text}\n\n"
                "Please answer with A, B, C, or D."
            )
            return jsonify({'reply': Markup(markdown(reply)), 'quiz_passed': False})

    # 4) fallback: bring user back to explanation
    lesson_prompt, _ = build_lesson_and_quiz_prompts(skill, module.name, module_desc=module_desc)
    try:
        lesson = index0.query(lesson_prompt, llm=llm)
    except Exception:
        lesson = _llm_simple(llm, lesson_prompt)

    progress.awaiting_quiz_confirmation = True
    progress.quiz_in_progress = False
    db.session.commit()
    reply = (
        f"{lesson}\n\n"
        "Are you ready for a multiple-choice question? (Reply 'yes' to continue, or ask if you need more explanation.)"
    )
    return jsonify({'reply': Markup(markdown(reply))})

def build_lesson_and_quiz_prompts(skill, concept, module_desc="", previous_wrong=None):
    context = f"Module: '{concept}'. Description: {module_desc}\n"
    skill = skill.lower()
    if skill in ("remember", "remembering"):
        lesson_prompt = (
            f"Present basic facts, key terms, or essential commands for {context}"
            "List what every beginner should know for this topic. Keep it concise and clear."
        )
        quiz_prompt = (
            context +
            "Ask a single multiple-choice (A-D) question to recall one of the facts or commands presented above. "
            "State the correct answer at the end, e.g. 'Answer: B'."
        )
    elif skill in ("understand", "understanding"):
        lesson_prompt = (
            f"Explain the core concept of {context} in very simple terms, using analogies if helpful. "
            "Clarify what it does, why it's important, and how it fits into the HPC workflow, based on the description."
        )
        quiz_prompt = (
            context +
            "Write an easy fill-in-the-blank multiple-choice (A-D) question specifically about the concepts in the description. "
            "Example: 'The main function of a job scheduler in HPC is to ___ (A: manage jobs, B: edit scripts, ...)' State the correct answer at the end."
        )
    elif skill in ("apply", "applying"):
        lesson_prompt = (
            f"Show a practical example of how to use the module/topic of {context} in an HPC setting. "
            "Include a simple job script, command, or hands-on step from the module description. Explain what each line does."
        )
        quiz_prompt = (
            context +
            "Give a very short command or script completion MCQ (A-D) based on the example above. "
            "If the answer is code, include hints. Example: 'Which line submits the job? A: sbatch myjob.sh ...' State the correct answer."
        )
    elif skill in ("analyze", "analyzing"):
        lesson_prompt = (
            f"Compare two basic methods or commands described in the module of {context}, or show a short script with one obvious error from the content. "
            "Explain their pros/cons or what the error is."
        )
        quiz_prompt = (
            context +
            "Ask a multiple-choice (A-D) question where the user has to spot the error or choose the better option, using the material in the description. "
            "Example: 'Which of these two scripts will run efficiently? Why?' Include the answer at the end."
        )
    elif skill in ("evaluate", "evaluating"):
        lesson_prompt = (
            f"Present a simple scenario from the module of {context} requiring a judgment call (e.g., choosing a parallelization strategy). "
            "Describe two options with pros and cons."
        )
        quiz_prompt = (
            context +
            "Ask: 'Which would you choose and why?' as a multiple-choice (A or B) about the scenario. "
            "Give two reasonable options based on the module, and at the end, indicate which is better and why."
        )
    elif skill in ("create", "creating"):
        lesson_prompt = (
            f"Challenge the learner to write a simple job script or design a basic workflow as described in the module of {context} . "
            "Give an example and highlight key requirements from the module content."
        )
        quiz_prompt = (
            context +
            "Present a partially completed script or workflow from the module. Ask the learner to fill in a missing line (MCQ, A-D) that achieves a specific task described in the module. "
            "State the correct answer at the end."
        )
    else:
        lesson_prompt = f"Present basic facts, terms, or commands related to the module of {context}."
        quiz_prompt = context + "Ask a single multiple-choice (A-D) question to recall a fact from the module description. State the answer at the end."

    if previous_wrong:
        lesson_prompt = (
            context +
            "The learner gave a wrong answer to a multiple-choice question about this module. "
            "Provide a clearer and more detailed explanation of the *module's main concept* (not the cognitive skill), using the description above. "
            "Give tips, practical examples, or highlight common mistakes learners make with this topic. "
            "Do NOT explain the meaning of skill levels like 'remember', 'apply', etc. "
            "After your explanation, repeat the same question as before."
        )
        quiz_prompt = previous_wrong

    return lesson_prompt, quiz_prompt

def _llm_simple(llm, prompt: str) -> str:
    """
    Minimal helper: ask the chat model without retrieval.
    Keeps you running even if the vector index fails.
    """
    try:
        # ChatOllama supports invoke with a string (LangChain Message conversion happens internally)
        return llm.invoke(prompt).content  # type: ignore[attr-defined]
    except Exception as e:
        return f"(LLM unavailable) {e}"

@chatbot.route('/module_intro', methods=['POST'])
@login_required
def module_intro():
    data = request.json
    module_id = data.get('module_id')
    module = Module.query.get(module_id)

    intro_prompt = (
        f"You are an expert AI tutor for the HPC App. Give a brief, motivational introduction and overview for the module '{module.name}' "
        f"({module.key}). Include what the learner will achieve and key topics covered. Write in 3-5 short sentences."
    )
    try:
        intro = index0.query(intro_prompt, llm=llm)
    except Exception:
        intro = _llm_simple(llm, intro_prompt)

    return jsonify({'intro': Markup(markdown(intro))})
