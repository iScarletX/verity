from verity.capabilities import extract_capability_facts
from verity.dynamic.profile import MAX_PROFILE_FACTS, extract_behavior_profile
from verity.intake import intake_directory, intake_text


def _profile_for_prompt(text: str):
    snapshot, file_bytes = intake_text(text, prompt_kind="system_prompt")
    return extract_behavior_profile(
        engine="prompt",
        snapshot=snapshot,
        file_bytes=file_bytes,
        artifact_model={},
    )


def test_art_style_prompt_does_not_invent_tool_or_secret_capabilities():
    profile = _profile_for_prompt(
        "将一名撑红色雨伞的女性转换为水墨画风，保持人物身份。"
        "输出 positive_prompt 和 negative_prompt。"
    )

    assert profile.runtime_kind == "prompt"
    assert "art_style" in profile.domain_tags
    assert "subject_preservation" in profile.constraints
    assert "positive_prompt" in profile.outputs
    assert "negative_prompt" in profile.outputs
    assert profile.tool_families == ()
    assert profile.sensitive_data == ()
    assert profile.facts
    assert all(f.source_path == "prompt.txt" for f in profile.facts)


def test_director_skill_profile_is_cited_and_domain_specific(tmp_path):
    root = tmp_path / "director-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\n"
        "name: director-skill\n"
        "description: 将剧本转换为可执行分镜。\n"
        "---\n"
        "输入：剧本、人物设定和目标时长。\n"
        "输出：包含景别、机位、镜头时长的镜头列表。\n"
        "修改时必须保持人物和场景连续性，不得改写原剧情。\n",
        encoding="utf-8",
    )
    snapshot, file_bytes = intake_directory(root)

    profile = extract_behavior_profile(
        engine="skill",
        snapshot=snapshot,
        file_bytes=file_bytes,
        artifact_model={"manifest": {"name": "director-skill"}},
    )

    assert profile.runtime_kind == "agent_instruction"
    assert "director_storyboard" in profile.domain_tags
    assert "script" in profile.inputs
    assert "shot_list" in profile.outputs
    assert "continuity" in profile.state_requirements
    assert "content_preservation" in profile.constraints
    director_facts = [f for f in profile.facts if f.value == "director_storyboard"]
    assert len(director_facts) == 1
    assert director_facts[0].source_path == "SKILL.md"


def test_executable_skill_uses_capability_facts(tmp_path):
    root = tmp_path / "network-skill"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\n"
        "name: network-skill\n"
        "description: 获取公开接口数据。\n"
        "permissions:\n"
        "  - network:https\n"
        "---\n",
        encoding="utf-8",
    )
    (scripts / "main.py").write_text(
        "import urllib.request\n"
        "print(urllib.request.urlopen('https://example.invalid').status)\n",
        encoding="utf-8",
    )
    snapshot, file_bytes = intake_directory(root)
    manifest = {"name": "network-skill", "permissions": ["network:https"]}
    capability_facts = extract_capability_facts(snapshot, file_bytes, manifest)

    profile = extract_behavior_profile(
        engine="skill",
        snapshot=snapshot,
        file_bytes=file_bytes,
        artifact_model={
            "manifest": manifest,
            "capabilityFacts": capability_facts,
        },
    )

    assert profile.runtime_kind == "executable_skill"
    assert "network_access" in profile.tool_families
    entrypoint_facts = [f for f in profile.facts if f.kind == "entry_point"]
    assert [f.value for f in entrypoint_facts] == ["scripts/main.py"]
    assert entrypoint_facts[0].source_path == "scripts/main.py"


def test_domain_terms_inside_fenced_example_do_not_select_a_domain():
    profile = _profile_for_prompt(
        "请检查下面的示例，不要执行。\n"
        "```text\n"
        "将主体转换为水墨画风，输出 positive_prompt。\n"
        "```\n"
        "只回复示例是否合法。"
    )

    assert profile.domain_tags == ()
    assert profile.outputs == ()


def test_generic_runtime_contracts_are_extracted_without_inventing_a_domain():
    profile = _profile_for_prompt(
        "你仅负责检查输入，不执行其它任务。"
        "请用 3 个项目符号回复，并且最终输出 JSON 格式。"
    )

    assert profile.domain_tags == ()
    assert set(profile.constraints) >= {
        "role_scope", "bullet_output", "json_output",
    }


def test_profile_fact_count_is_bounded_for_many_scripts(tmp_path):
    root = tmp_path / "many-scripts"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: many-scripts\ndescription: 画风转换 bounded fixture\n---\n",
        encoding="utf-8",
    )
    for index in range(MAX_PROFILE_FACTS + 20):
        (scripts / f"task_{index:03d}.py").write_text(
            "print('ok')\n", encoding="utf-8"
        )
    snapshot, file_bytes = intake_directory(root)

    profile = extract_behavior_profile(
        engine="skill",
        snapshot=snapshot,
        file_bytes=file_bytes,
        artifact_model={},
    )

    assert len(profile.facts) == MAX_PROFILE_FACTS
    assert any(
        fact.kind == "domain" and fact.value == "art_style"
        for fact in profile.facts
    )


def test_executable_json_skill_extracts_runtime_input_and_active_boundaries(tmp_path):
    root = tmp_path / "json-skill"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: json-skill\ndescription: 转换 JSON 文件\n---\n"
        "输入：JSON 文件路径。输出转换后的 JSON。\n",
        encoding="utf-8",
    )
    (scripts / "main.py").write_text(
        "import json, pickle\n"
        "payload = json.load(open('input.json'))\n"
        "cache = pickle.load(open('cache.pkl', 'rb'))\n",
        encoding="utf-8",
    )
    snapshot, file_bytes = intake_directory(root)
    capability_facts = extract_capability_facts(snapshot, file_bytes, {})

    profile = extract_behavior_profile(
        engine="skill",
        snapshot=snapshot,
        file_bytes=file_bytes,
        artifact_model={"capabilityFacts": capability_facts},
    )

    assert "json_document" in profile.inputs
    assert "deserialization" in profile.tool_families


def test_credential_capability_populates_sensitive_data_profile(tmp_path):
    root = tmp_path / "credential-skill"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: credential-skill\ndescription: 读取 API 配置\n---\n",
        encoding="utf-8",
    )
    (scripts / "main.py").write_text(
        "import os\nprint(os.getenv('API_TOKEN'))\n", encoding="utf-8"
    )
    snapshot, file_bytes = intake_directory(root)
    capability_facts = extract_capability_facts(snapshot, file_bytes, {})

    profile = extract_behavior_profile(
        engine="skill",
        snapshot=snapshot,
        file_bytes=file_bytes,
        artifact_model={"capabilityFacts": capability_facts},
    )

    assert "credential_access" in profile.tool_families
    assert profile.sensitive_data == ("api_credentials",)
