import math
import pytest
from termstory.database import Database
from termstory.models import Project, Session, Command

# Helper Mock class for numpy during tests
class MockLinalg:
    @staticmethod
    def norm(v):
        return math.sqrt(sum(x * x for x in v))


class MockNp:
    linalg = MockLinalg
    ndarray = list

    @staticmethod
    def dot(v1, v2):
        return sum(x * y for x, y in zip(v1, v2))


# Helper Mock class for SentenceTransformer during tests
class MockSentenceTransformer:
    def __init__(self, model_name: str):
        self.model_name = model_name

    def encode(self, texts: list, **kwargs):
        # Return dummy embeddings (list of lists of floats)
        embeddings = []
        for text in texts:
            text_lower = text.lower()
            if "docker" in text_lower:
                embeddings.append([1.0, 0.0, 0.0])
            elif "pytest" in text_lower or "test" in text_lower:
                embeddings.append([0.0, 1.0, 0.0])
            else:
                embeddings.append([0.0, 0.0, 1.0])
        return embeddings


def test_tokenize():
    from termstory.rag import tokenize
    assert tokenize("Hello, World! 123") == ["hello", "world", "123"]
    assert tokenize("git commit -m 'feat: batch 9'") == ["git", "commit", "m", "feat", "batch", "9"]
    assert tokenize("") == []


def test_bm25_ranking():
    from termstory.rag import BM25, tokenize
    corpus = [
        tokenize("docker compose up and docker ps"),
        tokenize("pytest tests/ -v --tb=short"),
        tokenize("git commit -m 'update docs'"),
    ]
    bm25 = BM25(corpus)
    
    # Query "docker" should rank doc 0 first
    query_docker = tokenize("docker")
    scores_docker = [bm25.get_score(i, query_docker) for i in range(len(corpus))]
    assert scores_docker[0] > scores_docker[1]
    assert scores_docker[0] > scores_docker[2]

    # Query "pytest" should rank doc 1 first
    query_pytest = tokenize("pytest")
    scores_pytest = [bm25.get_score(i, query_pytest) for i in range(len(corpus))]
    assert scores_pytest[1] > scores_pytest[0]
    assert scores_pytest[1] > scores_pytest[2]


def test_cosine_similarity(monkeypatch):
    monkeypatch.setattr("termstory.rag.np", MockNp)
    from termstory.rag import cosine_similarity
    
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    v3 = [0.0, 1.0, 0.0]
    v4 = [0.0, 0.0, 0.0]

    # If numpy is not available, we temporarily mock it to test math logic
    # otherwise we can test it using list-based fallback if numpy was mocked
    assert abs(cosine_similarity(v1, v2) - 1.0) < 1e-9
    assert abs(cosine_similarity(v1, v3) - 0.0) < 1e-9
    assert cosine_similarity(v1, v4) == 0.0


def test_hybrid_search_raises_importerror_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr("termstory.rag.SENTENCE_TRANSFORMERS_AVAILABLE", False)
    db_file = tmp_path / "test_rag_err.db"
    db = Database(str(db_file))
    db.init_db()

    from termstory.rag import hybrid_search
    with pytest.raises(ImportError) as excinfo:
        hybrid_search(db, "docker")
    assert "sentence-transformers" in str(excinfo.value)


def test_hybrid_search_success_with_mocked_dependency(tmp_path, monkeypatch):
    # Setup mock sentence-transformers & numpy
    monkeypatch.setattr("termstory.rag.SENTENCE_TRANSFORMERS_AVAILABLE", True)
    monkeypatch.setattr("termstory.rag.SentenceTransformer", MockSentenceTransformer, raising=False)
    monkeypatch.setattr("termstory.rag.np", MockNp)
    
    db_file = tmp_path / "test_rag_success.db"
    db = Database(str(db_file))
    db.init_db()
    
    now = 1780000000
    p1 = Project(id=1, name="Docker Registry", path="~/projects/docker", first_seen=now, last_seen=now, session_count=1, total_time=100)
    p2 = Project(id=2, name="Test Runner", path="~/projects/tests", first_seen=now, last_seen=now, session_count=1, total_time=150)
    
    cmd1 = Command(timestamp=now, command="docker ps -a", session_id=1, project_id=1)
    s1 = Session(id=1, start_time=now, end_time=now + 100, duration_seconds=100, project_id=1, commands=[cmd1])
    s1.ai_summary = "Running docker daemon container checks"
    
    cmd2 = Command(timestamp=now + 5000, command="pytest tests/", session_id=2, project_id=2)
    s2 = Session(id=2, start_time=now + 5000, end_time=now + 5100, duration_seconds=100, project_id=2, commands=[cmd2])
    s2.ai_summary = "Running pytest tests suite"
    
    db.save_data([p1, p2], [s1, s2], [cmd1, cmd2])
    
    from termstory.rag import hybrid_search
    
    # Searching for "docker"
    results_docker = hybrid_search(db, "docker", alpha=0.5)
    assert len(results_docker) == 2
    # The first one should be Docker Registry since it has "docker" in project name, commands, summary
    assert results_docker[0]["session_id"] == 1
    assert results_docker[0]["project_name"] == "Docker Registry"
    assert "docker ps -a" in results_docker[0]["matching_commands"]
    assert results_docker[0]["hybrid_score"] > results_docker[1]["hybrid_score"]

    # Searching for "pytest"
    results_pytest = hybrid_search(db, "pytest", alpha=0.5)
    assert len(results_pytest) == 2
    assert results_pytest[0]["session_id"] == 2
    assert results_pytest[0]["project_name"] == "Test Runner"
    assert "pytest tests/" in results_pytest[0]["matching_commands"]
    assert results_pytest[0]["hybrid_score"] > results_pytest[1]["hybrid_score"]


def test_cli_search_semantic_success(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from termstory.cli import app
    
    db_file = tmp_path / "test_cli_search_sem.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.run_ingestion", lambda db: None)
    
    mock_results = [{
        "session_id": 42,
        "start_time": 1780000000,
        "end_time": 1780000100,
        "duration_seconds": 100,
        "project_id": 1,
        "project_name": "Mocked Project",
        "project_path": "~/mock",
        "ai_summary": "Semantic RAG Search Summary",
        "all_commands": ["pytest", "Semantic RAG Search Summary"],
        "matching_commands": ["pytest", "Semantic RAG Search Summary"],
        "all_commits": [],
        "matching_commits": [],
        "hybrid_score": 0.95
    }]
    
    monkeypatch.setattr("termstory.rag.SENTENCE_TRANSFORMERS_AVAILABLE", True)
    monkeypatch.setattr("termstory.rag.hybrid_search", lambda *args, **kwargs: mock_results, raising=False)
    
    runner = CliRunner()
    result = runner.invoke(app, ["search", "health", "--semantic", "--detailed"])
    assert result.exit_code == 0
    assert "Mocked Project" in result.stdout
    assert "Semantic RAG Search Summary" in result.stdout


def test_cli_search_semantic_missing_dependency(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from termstory.cli import app
    
    db_file = tmp_path / "test_cli_search_sem_err.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.run_ingestion", lambda db: None)
    
    monkeypatch.setattr("termstory.rag.SENTENCE_TRANSFORMERS_AVAILABLE", False)
    
    runner = CliRunner()
    result = runner.invoke(app, ["search", "health", "--semantic"])
    assert result.exit_code == 1
    assert "sentence-transformers" in result.stdout.lower()


def test_cli_search_semantic_missing_query(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from termstory.cli import app
    
    db_file = tmp_path / "test_cli_search_sem_no_q.db"
    monkeypatch.setattr("termstory.cli.get_db_path", lambda: str(db_file))
    monkeypatch.setattr("termstory.cli.run_ingestion", lambda db: None)
    
    runner = CliRunner()
    result = runner.invoke(app, ["search", "--semantic"])
    assert result.exit_code == 1
    assert "semantic search requires a search query" in result.stdout.lower()
