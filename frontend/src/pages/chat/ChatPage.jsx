import { Fragment, useEffect, useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { postJson } from "../../api/client";
import useApi, { POLL_MS } from "../../hooks/useApi";
import Button from "../../components/button/Button";
import ErrorBox from "../../components/errorbox/ErrorBox";
import Loading from "../../components/loading/Loading";
import PageTitle from "../../components/pagetitle/PageTitle";
import StatusBadge from "../../components/statusbadge/StatusBadge";
import styles from "./ChatPage.module.css";

// 대화 (SCR-003)
// data = { messages: [{ id, sender, kind, body, created_at, author }], questions: [{ id, body, status, required }],
//          summary: { features, feature_items: [{id, name}], open_questions, unknown_count, planning_version },
//          processing, locked, error }
export default function ChatPage() {
  const { project, reloadProject } = useOutletContext();
  const path = `/projects/${project.id}/messages`;
  const [, error, data, reload] = useApi(path, POLL_MS);
  const [text, setText] = useState("");
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const log = useRef(null);
  const composer = useRef(null);

  // 새 메시지가 오면 대화 기록 상자를 맨 아래로
  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [data?.messages.length, data?.processing]);

  if (!data) return <div className={styles.page}>{error ? <ErrorBox error={error} /> : <Loading />}</div>;

  const s = data.summary;
  const hasDraft = s.planning_version !== null;
  const disabled = data.locked || data.processing || busy;
  // 필수 질문을 먼저 하나씩 보여 준다 (피그마 "확인할 내용")
  const open = data.questions.filter((q) => q.status === "open").sort((a, b) => Number(b.required) - Number(a.required));
  const question = open[0];

  // 메시지를 보내고 대화와 프로젝트 상태를 다시 불러온다
  const sendMessage = async (body) => {
    setBusy(true);
    setActionError("");
    try {
      await postJson(path, body);
      await reload();
      await reloadProject();
      return true;
    } catch (e) {
      setActionError(e.message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  // 초안이 있으면 수정 요청, 없으면 서비스 설명으로 보낸다
  const handleSubmit = async (e) => {
    e?.preventDefault();
    if (text.trim().length === 0 || disabled) return;
    const ok = await sendMessage({ kind: hasDraft ? "revision" : "description", body: text });
    if (ok) setText("");
  };

  // Enter는 보내기, Shift + Enter는 줄바꿈
  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleAnswer = async () => {
    const body = answer.trim();
    if (!question || body.length === 0) return;
    const ok = await sendMessage({ kind: "answer", body, answers: [{ question_id: question.id }] });
    if (ok) setAnswer("");
  };

  const badge = data.processing ? <span className={styles.processing}>처리 중</span> : <StatusBadge status={project.status} />;

  // 확정 뒤 안내 (피그마 "확정 뒤 안내"): 대화 입력 대신 변경 요청으로 안내
  if (data.locked) {
    return (
      <div className={styles.page}>
        <PageTitle title="대화" sub={summaryText(s)} />
        <section className={styles.lockedBox}>
          <p className={styles.lockedTitle}>기획서가 확정된 뒤 보낸 내용은 변경 요청으로 등록됩니다.</p>
          <p className={styles.help}>기존 승인 버전은 직접 수정하지 않습니다. 변경 범위와 다시 승인할 지점을 먼저 확인합니다.</p>
        </section>
        <div>
          <Button variant="secondary" to={`/projects/${project.id}/changes`} className={styles.wideButton}>
            변경 요청 보기
          </Button>
        </div>
        <MessageLog messages={data.messages} />
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <PageTitle
        title="대화"
        sub={data.processing ? "에이전트가 요구사항을 정리하고 있습니다." : summaryText(s)}
        actions={badge}
      />

      {/* 추출 실패 (피그마 "추출 실패") */}
      {data.error && !data.processing && (
        <section className={styles.failBox}>
          <p className={styles.failTitle}>요구사항 정리에 실패했습니다</p>
          <p className={styles.help}>
            실패한 항목: {data.error}
            <br />
            기존 대화와 정리된 기능은 유지됩니다.
          </p>
          <Button variant="secondary" onClick={() => composer.current?.focus()} className={styles.wideButton}>
            다시 입력하기
          </Button>
        </section>
      )}

      <div className={styles.layout}>
        <div className={styles.left}>
          <section className={styles.log} ref={log}>
            {data.messages.length === 0 && (
              <p className={styles.help}>만들고 싶은 서비스를 설명해 주세요. 누가, 무엇을, 왜 하려는지 적으면 좋습니다.</p>
            )}
            {data.messages.map((m, i) => (
              <Fragment key={m.id}>
                {m.sender === "agent" && i > 0 && <hr className={styles.divider} />}
                <div className={styles.message}>
                  <p className={m.sender === "agent" ? styles.agentName : styles.userName}>
                    {m.sender === "agent" ? "Orbit" : m.author ?? "나"}
                  </p>
                  <p className={styles.body}>{m.body}</p>
                </div>
              </Fragment>
            ))}
            {data.processing && (
              <>
                {data.messages.length > 0 && <hr className={styles.divider} />}
                <div className={styles.message}>
                  <p className={styles.agentName}>Orbit</p>
                  <p className={`${styles.body} ${styles.muted}`}>요구사항을 정리하고 있습니다…</p>
                </div>
              </>
            )}
            {hasDraft && !data.processing && (
              <Button variant="secondary" to={`/projects/${project.id}/planning`} className={styles.draftButton}>
                기획서 초안 보기
              </Button>
            )}
          </section>

          <form className={styles.composer} onSubmit={handleSubmit}>
            <textarea
              ref={composer}
              rows={2}
              placeholder={
                data.processing
                  ? "정리가 끝날 때까지 기다려 주세요"
                  : data.messages.length === 0
                    ? "예: 팀원이 웹에서 회의실을 골라 시간대를 예약하게 해 줘."
                    : "추가하거나 변경할 내용을 입력하세요"
              }
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={disabled}
            />
            <div className={styles.composerBottom}>
              <span className={styles.small}>Shift + Enter로 줄바꿈</span>
              <Button disabled={disabled || !text.trim()} className={styles.sendButton}>
                보내기
              </Button>
            </div>
          </form>
          <ErrorBox error={actionError} />
        </div>

        <aside className={styles.side}>
          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>확인할 내용</h2>
            {question ? (
              <>
                <span className={`${styles.tag} ${question.required ? styles.required : ""}`}>
                  {question.required ? "필수" : "선택"}
                </span>
                <p className={styles.question}>{question.body}</p>
                <label className={styles.answerLabel}>
                  답변
                  <textarea
                    rows={2}
                    value={answer}
                    onChange={(e) => setAnswer(e.target.value)}
                    placeholder="답변을 입력하세요"
                    disabled={disabled}
                  />
                </label>
                <Button block disabled={disabled || !answer.trim()} onClick={handleAnswer}>
                  답변 보내기
                </Button>
                {open.length > 1 && <p className={styles.small}>남은 질문 {open.length - 1}개 더</p>}
              </>
            ) : (
              <p className={styles.help}>
                {data.processing ? "정리가 끝나면 확인할 질문이 여기에 표시됩니다." : "지금은 확인할 질문이 없습니다."}
              </p>
            )}
          </section>

          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>정리한 기능</h2>
            {s.feature_items?.length ? (
              <ul className={styles.features}>
                {s.feature_items.map((f) => (
                  <li key={f.id ?? f.name}>
                    {f.id && <span className={styles.featureId}>{f.id}</span>}
                    {f.name}
                  </li>
                ))}
              </ul>
            ) : (
              <p className={styles.help}>정리가 끝나면 표시됩니다.</p>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}

// "기능 5개 · 남은 질문 1개 · 미정 항목 1개"
function summaryText(s) {
  return `기능 ${s.features.length}개  ·  남은 질문 ${s.open_questions}개  ·  미정 항목 ${s.unknown_count}개`;
}

// 확정 뒤 화면 아래에 지난 대화를 읽기 전용으로 보여 준다
function MessageLog({ messages }) {
  if (messages.length === 0) return null;
  return (
    <section className={styles.log}>
      {messages.map((m, i) => (
        <Fragment key={m.id}>
          {m.sender === "agent" && i > 0 && <hr className={styles.divider} />}
          <div className={styles.message}>
            <p className={m.sender === "agent" ? styles.agentName : styles.userName}>
              {m.sender === "agent" ? "Orbit" : m.author ?? "나"}
            </p>
            <p className={styles.body}>{m.body}</p>
          </div>
        </Fragment>
      ))}
    </section>
  );
}
