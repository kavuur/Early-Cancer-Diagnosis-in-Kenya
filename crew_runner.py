from crewai import Crew, Task 
from agent_loader import load_llm, load_agents_from_yaml, load_tasks_from_yaml
from datetime import datetime
import json
import re
from medical_case_faiss import MedicalCaseFAISS

AGENT_PATH = 'config/agents.yaml'
TASK_PATH = 'config/tasks.yaml'

# Load FAISS once
faiss_system = MedicalCaseFAISS()
faiss_system.load_index('medical_cases.index', 'medical_cases_metadata.pkl')

def run_task(agent, input_text, name="Step"):
    crew = Crew(
        agents=[agent],
        tasks=[Task(
            name=name,
            description=input_text,
            expected_output="Give your response as if you were in the middle of the diagnostic session.",
            agent=agent
        )],
        verbose=False
    )
    result = crew.kickoff()
    return result if isinstance(result, str) else getattr(result, 'final_output', str(result))


# Helpers
def format_event(role, message):
    return "data: " + json.dumps({
        "role": role,
        "message": (message or "").strip(),
        "timestamp": datetime.now().strftime("%H:%M:%S")
    }) + "\n\n"

def format_event_recommender(english, swahili):
    return "data: " + json.dumps({
        "type": "question_recommender",
        "question": {
            "english": (english or "").strip(),
            "swahili": (swahili or "").strip()
        },
        "timestamp": datetime.now().strftime("%H:%M:%S"),
    }) + "\n\n"

def format_bilingual(english: str, swahili: str) -> str:
    return f"<strong>English:</strong><br>{english or '—'}<br><br><strong>Swahili:</strong><br>{swahili or '—'}"

def sse_message(role, message, log_hook=None, session_id=None):
    ts = datetime.now().strftime("%H:%M:%S")
    payload = {"role": role, "message": (message or "").strip(), "timestamp": ts}
    if log_hook:
        log_hook(session_id, role, payload["message"], ts, "message")
    return "data: " + json.dumps(payload) + "\n\n"

def sse_recommender(english, swahili, log_hook=None, session_id=None):
    ts = datetime.now().strftime("%H:%M:%S")
    payload = {
        "type": "question_recommender",
        "question": {"english": (english or "").strip(), "swahili": (swahili or "").strip()},
        "timestamp": ts,
    }
    if log_hook:
        msg = f"Recommended Q | EN: {payload['question']['english']} | SW: {payload['question']['swahili']}"
        log_hook(session_id, "Question Recommender", msg, ts, "question_recommender")
    return "data: " + json.dumps(payload) + "\n\n"



# Mode 1: Fully simulated
def simulate_agent_chat_stepwise(initial_message: str, turns: int = 6, language_mode: str = 'bilingual', log_hook=None, session_id=None):
    llm = load_llm()
    agents = load_agents_from_yaml(AGENT_PATH, llm)

    # FIRST: yield the patient's seed so we always log at least one row
    yield sse_message("Patient", initial_message, log_hook, session_id)

    # THEN: try retrieval; if it fails, continue gracefully
    similar_cases = []
    similar_bullets = ""
    try:
        similar_cases = faiss_system.search_similar_cases(initial_message, k=5, similarity_threshold=0.19) or []
        similar_bullets = "\n".join(
            f"- {getattr(c, 'title', 'Case')}: {getattr(c, 'summary', '')}" for c in similar_cases
        )
    except Exception as e:
        logger.exception("FAISS search failed during simulated mode; continuing without retrieval")

    context_log = [f"Patient says: {initial_message}"]
    if similar_bullets:
        context_log.append("Similar cases (context):\n" + similar_bullets)


    for turn in range(turns):
        # question recommender
        if language_mode == "english":
            recommender_input = "\n".join(context_log) + "\n\nSuggest the next most relevant diagnostic question. Format: English: ..."
        elif language_mode == "swahili":
            recommender_input = "\n".join(context_log) + "\n\nPendekeza swali fupi la uchunguzi linalofuata. Format: Swahili: ..."
        else:
            recommender_input = "\n".join(context_log) + "\n\nSuggest the next most relevant bilingual question only. Format as:\nEnglish: ...\n\nSwahili: ..."

        recommended = run_task(agents["question_recommender_agent"], recommender_input, f"Question Suggestion {turn+1}")

        if language_mode == "english":
            english_q, swahili_q = recommended.strip(), ""
        elif language_mode == "swahili":
            english_q, swahili_q = "", recommended.strip()
        else:
            match = re.search(r"English:\s*(.+?)\n+Swahili:\s*(.+)", recommended, re.DOTALL)
            if match:
                english_q, swahili_q = match.group(1).strip(), match.group(2).strip()
            else:
                english_q, swahili_q = recommended.strip(), ""

        
        if language_mode == "english":
            plain_q = f"{english_q}"
        elif language_mode == "swahili":
            plain_q = f"{swahili_q}"
        else:
            plain_q = f"{english_q}\n\n{swahili_q}"

        yield sse_recommender(english_q, swahili_q, log_hook, session_id)
        yield sse_message("Clinician", plain_q, log_hook, session_id)
        context_log.append(f"Clinician: {plain_q}")

        # simulated patient reply
        if language_mode == "english":
            patient_input = f"Clinician: {english_q}\n\nRespond in English as the patient. Be short and realistic."
        elif language_mode == "swahili":
            patient_input = f"Clinician: {swahili_q}\n\nJibu kwa Kiswahili kama mgonjwa. Toa jibu fupi na halisi."
        else:
            patient_input = f"Clinician: English: {english_q} Swahili: {swahili_q}\n\nRespond as the patient. Answer both languages if possible. Be short and realistic."

        patient_response = run_task(agents["patient_agent"], patient_input, f"Patient Response {turn+1}")
        yield sse_message("Patient", patient_response, log_hook, session_id)
        context_log.append(f"Patient: {patient_response}")

    # Finalize
    listener_input = "\n".join(context_log) + "\n\nSummarize the conversation in two parts:\n**English Summary:**\n- ...\n**Swahili Summary:**\n- ..."
    listener_summary = run_task(agents["listener_agent"], listener_input, "Listener Summary")
    yield sse_message("Listener", listener_summary, log_hook, session_id)

    final_input = listener_input + "\n\nProvide a FINAL PLAN clearly structured as bullet points. Format like:\n**FINAL PLAN:**\n- Step 1: ...\n- Step 2: ..."
    final_plan = run_task(agents["clinician_agent"], final_input, "Final Plan")
    yield sse_message("Clinician", f"**FINAL PLAN:**\n\n{final_plan}", log_hook, session_id)


# Mode 2: Real actors
def real_actor_chat_stepwise(initial_message: str, language_mode: str = 'bilingual', speaker_role: str = 'Patient', conversation_history: list | None = None, log_hook=None, session_id=None):
    """
    live mode.
    - Patient message triggers Question recommender
    - Clinician asks question based on question recommender
    - Finalize feeds conversation history to listener and clinician
    """
    llm = load_llm()
    agents = load_agents_from_yaml(AGENT_PATH, llm)
    history = conversation_history or []

    #def format_event_message(role, message):
        #payload = {
           # "type": "message",
           # "role": role,
            #"message": (message or "").strip(),
            #"timestamp": datetime.now().strftime("%H:%M:%S"),
       # }
        #return "data: " + json.dumps(payload) + "\n\n"

    # Finalize
    if speaker_role.lower() == "finalize":
        transcript_lines = [f"{m.get('role','')}: {m.get('message','')}" for m in history]
        convo_text = "\n".join(transcript_lines)

        listener_input = convo_text + "\n\nSummarize the conversation in two parts:\n**English Summary:**\n- ...\n**Swahili Summary:**\n- ..."
        listener_summary = run_task(agents["listener_agent"], listener_input, "Listener Summary")
        yield sse_message("Listener", listener_summary, log_hook, session_id)

        final_input = listener_input + "\n\nProvide a FINAL PLAN clearly structured as bullet points. Format like:\n**FINAL PLAN:**\n- Step 1: ...\n- Step 2: ..."
        final_plan = run_task(agents["clinician_agent"], final_input, "Final Plan")
        yield sse_message("Clinician", f"**FINAL PLAN**\n\n{final_plan}", log_hook, session_id)
        return

    yield sse_message(speaker_role, initial_message, log_hook, session_id)

    # After a Patient reply suggest next question
    if speaker_role.lower() == "patient":
        context_lines = [f"{m.get('role')}: {m.get('message')}" for m in history]
        context_text = "\n".join(context_lines)

        if language_mode == "english":
            recommender_input = context_text + "\n\nSuggest the next most relevant diagnostic question. Format: English: ..."
        elif language_mode == "swahili":
            recommender_input = context_text + "\n\nPendekeza swali fupi la uchunguzi linalofuata. Format: Swahili: ..."
        else:
            recommender_input = context_text + "\n\nSuggest the next most relevant bilingual question only. Format as:\nEnglish: ...\n\nSwahili: ..."

        rec = run_task(agents["question_recommender_agent"], recommender_input, "Question Suggestion")

        if language_mode == "english":
            english_q, swahili_q = rec.strip(), ""
        elif language_mode == "swahili":
            english_q, swahili_q = "", rec.strip()
        else:
            match = re.search(r"English:\s*(.+?)\n+Swahili:\s*(.+)", rec, re.DOTALL)
            if match:
                english_q, swahili_q = match.group(1).strip(), match.group(2).strip()
            else:
                english_q, swahili_q = rec.strip(), ""

        yield sse_recommender(english_q, swahili_q, log_hook, session_id)

    return


# --- NEW: Mode 3: Live transcription (continuous patient chunks → recommender only) ---
def live_transcription_stream(initial_message: str,
                              language_mode: str = 'bilingual',
                              speaker_role: str = 'patient',
                              conversation_history: list | None = None,
                              log_hook=None,
                              session_id=None):
    """
    Live transcription mode (final-driven).
    - Client streams audio chunks to /transcribe_chunk and shows partials locally.
    - On Stop, client calls /transcribe_finalize, then sends the FINAL text here once.
    - We yield:
        1) Patient(final) message
        2) One question recommendation based on full history + final
    - 'speaker_role=finalize' path still does Summary + Final Plan.
    """
    llm = load_llm()
    agents = load_agents_from_yaml(AGENT_PATH, llm)
    history = conversation_history or []

    # Finalize path (unchanged)
    if speaker_role.lower() == "finalize":
        transcript_lines = [f"{m.get('role','')}: {m.get('message','')}" for m in history]
        convo_text = "\n".join(transcript_lines)

        listener_input = convo_text + "\n\nSummarize the conversation in two parts:\n**English Summary:**\n- ...\n**Swahili Summary:**\n- ..."
        listener_summary = run_task(agents["listener_agent"], listener_input, "Listener Summary")
        yield sse_message("Listener", listener_summary, log_hook, session_id)

        final_input = listener_input + "\n\nProvide a FINAL PLAN clearly structured as bullet points. Format like:\n**FINAL PLAN:**\n- Step 1: ...\n- Step 2: ..."
        final_plan = run_task(agents["clinician_agent"], final_input, "Final Plan")
        yield sse_message("Clinician", f"**FINAL PLAN**\n\n{final_plan}", log_hook, session_id)
        return

    # Treat the incoming message as FINAL (we don't handle partials here)
    final_text = (initial_message or "").strip()
    if not final_text:
        return

    # 1) Emit patient final line
    yield sse_message("Patient", final_text, log_hook, session_id)

    # 2) Build recommender context from full history (now including this final)
    context_lines = [f"{m.get('role')}: {m.get('message')}" for m in history]
    context_text = "\n".join(context_lines)

    if language_mode == "english":
        recommender_input = context_text + "\n\nSuggest the next most relevant diagnostic question. Format: English: ..."
    elif language_mode == "swahili":
        recommender_input = context_text + "\n\nPendekeza swali fupi la uchunguzi linalofuata. Format: Swahili: ..."
    else:
        recommender_input = context_text + "\n\nSuggest the next most relevant bilingual question only. Format as:\nEnglish: ...\n\nSwahili: ..."

    rec = run_task(agents["question_recommender_agent"], recommender_input, "Question Suggestion")

    if language_mode == "english":
        english_q, swahili_q = rec.strip(), ""
    elif language_mode == "swahili":
        english_q, swahili_q = "", rec.strip()
    else:
        match = re.search(r"English:\s*(.+?)\n+Swahili:\s*(.+)", rec, re.DOTALL)
        if match:
            english_q, swahili_q = match.group(1).strip(), match.group(2).strip()
        else:
            english_q, swahili_q = rec.strip(), ""

    yield sse_recommender(english_q, swahili_q, log_hook, session_id)
    return




def simulate_agent_chat(user_message):
    llm = load_llm()
    agent_dict = load_agents_from_yaml(AGENT_PATH, llm)
    tasks = load_tasks_from_yaml(TASK_PATH, agent_dict)

    if not agent_dict or not tasks:
        return [{"role": "system", "message": "Error: No agents or tasks loaded."}]

    tasks[0].description += f"\n\nPatient says: {user_message}"

    crew = Crew(
        agents=list(agent_dict.values()),
        tasks=tasks,
        verbose=True
    )

    output = crew.kickoff()

    if isinstance(output, str):
        result_text = output
    elif hasattr(output, 'final_output'):
        result_text = output.final_output
    elif hasattr(output, 'result'):
        result_text = output.result
    else:
        result_text = str(output)

    transcript = [
        {"role": "Patient", "message": user_message, "timestamp": datetime.now().strftime("%H:%M:%S")},
        {"role": "Clinician", "message": result_text, "timestamp": datetime.now().strftime("%H:%M:%S")}
    ]

    return transcript
