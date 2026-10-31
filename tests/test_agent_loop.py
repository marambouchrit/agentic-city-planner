"""The loop must follow the LLM's decisions, and code must enforce the limits."""

from pydantic import BaseModel

from city_planner.agents.base import AgentConfig, CallBudget, Tool, run_agent, tool_declaration
from city_planner.llm import LLMError
from tests.fakes import FakeLLM, call


class Args(BaseModel):
    value: str


class Inner(BaseModel):
    place_id: str


class Answer(BaseModel):
    text: str


def agent(max_steps=5, ran=None, finalize=None):
    ran = [] if ran is None else ran

    def tool(name):
        def run(args):
            ran.append((name, args.value))
            return {"result": f"{name}:{args.value}"}

        return Tool(name, "A tool.", Args, run)

    return AgentConfig(
        name="Test",
        instructions="Be helpful.",
        tools=[tool("a"), tool("b")],
        finish_description="Finish.",
        finish_model=Answer,
        finalize=finalize or (lambda answer: answer.text),
        max_steps=max_steps,
    )


def run(script, budget=None, **kwargs):
    llm = FakeLLM(script)
    return run_agent(agent(**kwargs), "task", llm, budget or CallBudget()), llm


def test_code_follows_whatever_order_the_llm_chooses():
    for first, second in (("a", "b"), ("b", "a")):
        ran = []
        script = [[call(first, value="1")], [call(second, value="2")], [call("finish", text="done")]]
        result, _ = run(script, ran=ran)
        assert ran == [(first, "1"), (second, "2")]
        assert result.output == "done" and result.stop_reason == "finished"


def test_llm_can_finish_without_using_any_tool():
    ran = []
    result, _ = run([[call("finish", text="nothing needed")]], ran=ran)
    assert ran == [] and result.stop_reason == "finished"


def test_llm_can_repeat_a_tool_or_call_several_at_once():
    ran = []
    script = [[call("a", value="1"), call("a", value="2")], [call("a", value="3")], [call("finish", text="x")]]
    run(script, ran=ran)
    assert [value for _, value in ran] == ["1", "2", "3"]


def test_llm_reads_each_result_before_deciding_again():
    _, llm = run([[call("a", value="1")], [call("finish", text="x")]])
    assert llm.requests[1]["messages"][-1] == {"role": "tool", "results": [("a", {"result": "a:1"})]}


def test_agent_cannot_use_a_tool_it_does_not_have():
    result, llm = run([[call("optimize_schedule", value="1")], [call("finish", text="x")]])
    assert "no tool named" in llm.requests[1]["messages"][-1]["results"][0][1]["error"]
    assert result.trace[0].kind == "rejected"


def test_only_own_tools_and_finish_are_offered():
    _, llm = run([[call("finish", text="x")]])
    assert llm.requests[0]["tools"] == ["a", "b", "finish"]


def test_bad_arguments_go_back_to_the_llm():
    ran = []
    result, llm = run([[call("a", wrong="1")], [call("finish", text="x")]], ran=ran)
    assert ran == []
    assert "Invalid arguments" in llm.requests[1]["messages"][-1]["results"][0][1]["error"]
    assert result.stop_reason == "finished"


def test_invalid_final_answer_is_rejected_and_llm_can_fix_it():
    result, llm = run([[call("finish", wrong="x")], [call("finish", text="good")]])
    assert "rejected" in llm.requests[1]["messages"][-1]["results"][0][1]["error"]
    assert result.output == "good"


def test_finalize_can_reject_invented_data():
    def finalize(answer):
        if answer.text == "invented":
            raise ValueError("not from a tool")
        return answer.text

    result, _ = run([[call("finish", text="invented")], [call("finish", text="real")]], finalize=finalize)
    assert result.output == "real"


def test_finish_with_other_calls_is_refused_until_results_are_read():
    ran = []
    result, _ = run([[call("a", value="1"), call("finish", text="early")], [call("finish", text="late")]], ran=ran)
    assert ran == [("a", "1")]
    assert result.output == "late"


def test_text_answer_gets_a_reminder_and_uses_a_step():
    result, llm = run(["I think we are done", [call("finish", text="x")]])
    assert result.steps == 2
    assert "calling one of your tools" in llm.requests[1]["messages"][-1]["text"]


def test_last_step_only_allows_finish():
    _, llm = run([[call("a", value="1")], [call("finish", text="x")]], max_steps=2)
    assert llm.requests[0]["only_tools"] is None
    assert llm.requests[1]["only_tools"] == ["finish"]


def test_step_limit_stops_with_no_invented_output():
    result, llm = run([[call("a", value=str(i))] for i in range(3)], max_steps=3)
    assert result.stop_reason == "step_limit" and result.output is None
    assert len(llm.requests) == 3


def test_budget_is_shared_and_counts_calls():
    budget = CallBudget(limit=30)
    run([[call("a", value="1")], [call("finish", text="x")]], budget=budget)
    run([[call("finish", text="y")]], budget=budget)
    assert budget.used == 3


def test_empty_budget_makes_no_llm_call():
    result, llm = run([], budget=CallBudget(limit=0))
    assert result.stop_reason == "budget" and llm.requests == []


def test_last_budget_call_only_allows_finish():
    result, llm = run([[call("finish", text="x")]], budget=CallBudget(limit=1))
    assert llm.requests[0]["only_tools"] == ["finish"] and result.output == "x"


def test_llm_failure_stops_cleanly():
    result, _ = run([LLMError("HTTP 429: quota")])
    assert result.stop_reason == "llm_error" and result.output is None and "429" in result.error


def test_declaration_has_no_titles_defaults_or_references():
    class Outer(BaseModel):
        title: str
        items: list[Inner]
        food: str = ""

    declaration = tool_declaration("x", "X.", Outer)
    text = str(declaration["parameters"])
    assert "$ref" not in text and "$defs" not in text and "default" not in text
    assert "'title': 'Outer'" not in text
    # A field that happens to be called "title" must stay.
    assert "title" in declaration["parameters"]["properties"]
    assert declaration["parameters"]["properties"]["items"]["items"]["properties"]["place_id"]["type"] == "string"
    assert declaration["parameters"]["required"] == ["title", "items"]
