import pytest

from bbclaw.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_read_file_nonexistent_path_includes_actionable_guidance():
    registry = ToolRegistry()

    result = await registry.call("read_file", path="definitely_missing_dir/definitely_missing_file.txt")

    assert "Error" in result
    assert "definitely_missing_dir/definitely_missing_file.txt" in result
    assert "Path recibido" in result
    assert "Path normalizado" in result
    assert "list_files" in result


@pytest.mark.asyncio
async def test_read_source_nonexistent_path_includes_actionable_guidance():
    registry = ToolRegistry()

    result = await registry.call("read_source", path="definitely_missing_source_dir/definitely_missing_source_file.py")

    assert "Error" in result
    assert "definitely_missing_source_dir/definitely_missing_source_file.py" in result
    assert "Path recibido" in result
    assert "Path normalizado" in result
    assert "list_files" in result


@pytest.mark.asyncio
async def test_read_file_dot_path_is_normalized_and_guided():
    registry = ToolRegistry()

    result = await registry.call("read_file", path="./")

    assert "Error" in result
    assert "Path recibido: ./" in result
    assert "Path normalizado: ." in result
    assert "list_files" in result
