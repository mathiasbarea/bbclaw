import { useCallback, useEffect, useState, useRef, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Command } from 'lucide-react';
import { AgentAvatar } from './AgentAvatar';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface ChatMessage {
    id: string;
    role: 'user' | 'system';
    text: string;
    createdAt: number;
    requestId?: string;
    status?: 'pending' | 'completed' | 'failed';
    typewriter?: boolean;
}

interface SendMessageResult {
    text: string;
    requestId?: string;
    queued?: boolean;
    sessionId?: string;
}

interface RequestCompletionMessage {
    id: string;
    requestId: string;
    text: string;
    status: 'completed' | 'failed';
}

interface PendingRequestItem {
    requestId: string;
    prompt: string;
    createdAt: number;
}

interface ChatHistoryResponse {
    sessionId?: string | null;
    messages?: ChatMessage[];
    pendingRequests?: PendingRequestItem[];
    hasMore?: boolean;
    nextCursor?: string | null;
}

const INITIAL_HISTORY_LIMIT = 30;
const TOP_LOAD_THRESHOLD_PX = 36;
const TOP_CONTROL_VISIBILITY_THRESHOLD_PX = 120;
const AUTO_LOAD_COOLDOWN_MS = 420;

function isEditableElement(target: EventTarget | null): boolean {
    if (!(target instanceof HTMLElement)) return false;
    const tagName = target.tagName;
    return tagName === 'INPUT' || tagName === 'TEXTAREA' || target.isContentEditable;
}

function formatMessageTime(epochMs: number): string {
    if (!Number.isFinite(epochMs)) return '';
    return new Date(epochMs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function truncateText(text: string, maxLength: number): string {
    if (text.length <= maxLength) return text;
    return `${text.slice(0, Math.max(0, maxLength - 1)).trim()}...`;
}

function resolveMessageOrder(messageId: string): number {
    if (messageId.endsWith(':user')) return 0;
    if (messageId.endsWith(':initial')) return 1;
    if (messageId.endsWith(':final')) return 2;
    return 3;
}

function buildHistoryUrl(
    apiBaseUrl: string,
    options?: { beforeCursor?: string; sessionId?: string | null; includePreviousSessions?: boolean }
): string {
    const normalizedBase = apiBaseUrl.replace(/\/$/, '');
    const query = new URLSearchParams({
        channel: 'web',
        limit: String(INITIAL_HISTORY_LIMIT),
    });
    if (options?.sessionId) {
        query.set('session_id', options.sessionId);
    }
    if (options?.beforeCursor) {
        query.set('before', options.beforeCursor);
    }
    if (options?.includePreviousSessions) {
        query.set('include_previous_sessions', '1');
    }
    return `${normalizedBase}/api/chat/history?${query.toString()}`;
}

function normalizeHistoryMessages(items: ChatMessage[] | undefined): ChatMessage[] {
    const loadedMessages = Array.isArray(items)
        ? items
            .filter((item): item is ChatMessage => {
                if (!item || typeof item !== 'object') return false;
                if (typeof item.id !== 'string') return false;
                if (item.role !== 'user' && item.role !== 'system') return false;
                if (typeof item.text !== 'string') return false;
                if (typeof item.createdAt !== 'number' || !Number.isFinite(item.createdAt)) return false;
                return true;
            })
            .map((item) => ({
                ...item,
                typewriter: false,
            }))
        : [];
    loadedMessages.sort((a, b) => {
        if (a.createdAt !== b.createdAt) return a.createdAt - b.createdAt;
        const aRequestId = a.requestId || '';
        const bRequestId = b.requestId || '';
        if (aRequestId !== bRequestId) return aRequestId.localeCompare(bRequestId);
        const byOrder = resolveMessageOrder(a.id) - resolveMessageOrder(b.id);
        if (byOrder !== 0) return byOrder;
        return a.id.localeCompare(b.id);
    });
    return loadedMessages;
}

function normalizePendingRequests(items: PendingRequestItem[] | undefined): PendingRequestItem[] {
    return Array.isArray(items)
        ? items.filter((item): item is PendingRequestItem => {
            if (!item || typeof item !== 'object') return false;
            if (typeof item.requestId !== 'string' || !item.requestId.trim()) return false;
            if (typeof item.prompt !== 'string') return false;
            if (typeof item.createdAt !== 'number' || !Number.isFinite(item.createdAt)) return false;
            return true;
        })
        : [];
}

export function ChatInput({
    onSendMessage,
    agents = [],
    projects = [],
    requestCompletions = [],
    apiBaseUrl = '',
}: {
    onSendMessage: (msg: string, sessionId?: string) => Promise<SendMessageResult>;
    agents?: { name: string; role: string }[];
    projects?: { name: string }[];
    requestCompletions?: RequestCompletionMessage[];
    apiBaseUrl?: string;
}) {
    const [isVisible, setIsVisible] = useState(false);
    const [input, setInput] = useState('');
    const [isSending, setIsSending] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);
    const scrollRef = useRef<HTMLDivElement>(null);
    const shouldAutoScrollToBottomRef = useRef(false);
    const isLoadingOlderRef = useRef(false);
    const lastAutoOlderLoadAtRef = useRef(0);
    const pendingRequestIdsRef = useRef<Set<string>>(new Set());
    const consumedCompletionIdsRef = useRef<Set<string>>(new Set());

    const [history, setHistory] = useState<ChatMessage[]>([]);
    const [pendingRequests, setPendingRequests] = useState<PendingRequestItem[]>([]);
    const [sessionId, setSessionId] = useState<string | null>(null);
    const suppressHistoryReloadRef = useRef(false);
    const [historyNextCursor, setHistoryNextCursor] = useState<string | null>(null);
    const [historyHasMore, setHistoryHasMore] = useState(false);
    const [isLoadingOlder, setIsLoadingOlder] = useState(false);
    const [isHistoryNearTop, setIsHistoryNearTop] = useState(false);
    const [isHistoryScrollable, setIsHistoryScrollable] = useState(false);

    // Autocomplete state
    const [mentionType, setMentionType] = useState<'agent' | 'project' | null>(null);
    const [mentionIndex, setMentionIndex] = useState(-1);
    const [mentionQuery, setMentionQuery] = useState('');
    const [selectedIndex, setSelectedIndex] = useState(0);

    const filteredItems = mentionType === 'agent'
        ? agents.filter(a => a.name.toLowerCase().includes(mentionQuery.toLowerCase()))
        : mentionType === 'project'
            ? projects.filter(p => p.name.toLowerCase().includes(mentionQuery.toLowerCase()))
            : [];

    const updateMentionStateFromValue = useCallback((val: string) => {
        const atIndex = val.lastIndexOf('@');
        const hashIndex = val.lastIndexOf('#');
        const triggerIndex = Math.max(atIndex, hashIndex);

        if (triggerIndex !== -1 && (triggerIndex === 0 || val[triggerIndex - 1] === ' ')) {
            const type = val[triggerIndex] === '@' ? 'agent' : 'project';
            const spaceIndex = val.indexOf(' ', triggerIndex);
            const queryStr = spaceIndex === -1 ? val.slice(triggerIndex + 1) : '';
            if (spaceIndex === -1) {
                setMentionIndex(triggerIndex);
                setMentionType(type);
                setMentionQuery(queryStr);
                setSelectedIndex(0);
                return;
            }
        }

        setMentionIndex(-1);
        setMentionType(null);
    }, []);

    const refreshHistoryViewportFlags = useCallback(() => {
        const container = scrollRef.current;
        if (!container) {
            setIsHistoryNearTop(false);
            setIsHistoryScrollable(false);
            return;
        }

        const nearTop = container.scrollTop <= TOP_CONTROL_VISIBILITY_THRESHOLD_PX;
        const scrollable = container.scrollHeight > container.clientHeight + 2;
        setIsHistoryNearTop((current) => (current === nearTop ? current : nearTop));
        setIsHistoryScrollable((current) => (current === scrollable ? current : scrollable));
    }, []);

    useEffect(() => {
        if (!shouldAutoScrollToBottomRef.current) return;
        if (!isVisible) return;
        if (!scrollRef.current) return;
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        shouldAutoScrollToBottomRef.current = false;
        refreshHistoryViewportFlags();
    }, [history, isVisible, refreshHistoryViewportFlags]);

    useEffect(() => {
        if (!isVisible) return;
        let raf1 = 0;
        let raf2 = 0;
        raf1 = window.requestAnimationFrame(() => {
            raf2 = window.requestAnimationFrame(() => {
                const container = scrollRef.current;
                if (!container) return;
                container.scrollTop = container.scrollHeight;
                refreshHistoryViewportFlags();
            });
        });

        return () => {
            if (raf1) window.cancelAnimationFrame(raf1);
            if (raf2) window.cancelAnimationFrame(raf2);
        };
    }, [isVisible, refreshHistoryViewportFlags]);

    useEffect(() => {
        if (isVisible) return;
        setIsHistoryNearTop(false);
        setIsHistoryScrollable(false);
    }, [isVisible]);

    useEffect(() => {
        // Skip reload when sessionId changed as a result of sending a message
        // (the local state already has the latest messages with typewriter animation)
        if (suppressHistoryReloadRef.current) {
            suppressHistoryReloadRef.current = false;
            return;
        }

        let canceled = false;

        const loadHistory = async (): Promise<void> => {
            try {
                const response = await fetch(
                    buildHistoryUrl(apiBaseUrl, { sessionId, includePreviousSessions: true })
                );
                if (!response.ok) return;
                const payload = await response.json() as ChatHistoryResponse;
                if (canceled) return;

                const loadedMessages = normalizeHistoryMessages(payload.messages);
                shouldAutoScrollToBottomRef.current = true;
                setHistory(loadedMessages.slice(-INITIAL_HISTORY_LIMIT));

                const loadedPending = normalizePendingRequests(payload.pendingRequests);
                setPendingRequests(loadedPending);
                pendingRequestIdsRef.current = new Set(loadedPending.map((item) => item.requestId));
                const loadedSessionId = typeof payload.sessionId === 'string' && payload.sessionId.trim()
                    ? payload.sessionId.trim()
                    : null;
                if (loadedSessionId) {
                    setSessionId(loadedSessionId);
                }
                setHistoryHasMore(payload.hasMore === true);
                const nextCursor = typeof payload.nextCursor === 'string' && payload.nextCursor.trim()
                    ? payload.nextCursor.trim()
                    : null;
                setHistoryNextCursor(nextCursor);
            } catch {
                // ignore history loading errors
            }
        };

        loadHistory();

        return () => {
            canceled = true;
        };
    }, [apiBaseUrl, sessionId]);

    const handleLoadOlder = async () => {
        if (isLoadingOlderRef.current) return;
        if (!historyHasMore) return;
        if (!historyNextCursor) return;

        isLoadingOlderRef.current = true;
        setIsLoadingOlder(true);
        const container = scrollRef.current;
        const previousHeight = container?.scrollHeight || 0;
        const previousTop = container?.scrollTop || 0;

        try {
            const response = await fetch(
                buildHistoryUrl(apiBaseUrl, {
                    beforeCursor: historyNextCursor,
                    sessionId,
                    includePreviousSessions: true,
                })
            );
            if (!response.ok) {
                return;
            }

            const payload = await response.json() as ChatHistoryResponse;
            const olderMessages = normalizeHistoryMessages(payload.messages);
            const loadedSessionId = typeof payload.sessionId === 'string' && payload.sessionId.trim()
                ? payload.sessionId.trim()
                : null;
            if (loadedSessionId && loadedSessionId !== sessionId) {
                setSessionId(loadedSessionId);
            }
            shouldAutoScrollToBottomRef.current = false;
            setHistory((current) => {
                if (olderMessages.length === 0) return current;
                const existingIds = new Set(current.map((message) => message.id));
                const olderUnique = olderMessages.filter((message) => !existingIds.has(message.id));
                if (olderUnique.length === 0) return current;
                return [...olderUnique, ...current];
            });

            const nextCursor = typeof payload.nextCursor === 'string' && payload.nextCursor.trim()
                ? payload.nextCursor.trim()
                : null;
            setHistoryNextCursor(nextCursor);
            setHistoryHasMore(payload.hasMore === true);

            requestAnimationFrame(() => {
                const updatedContainer = scrollRef.current;
                if (!updatedContainer) return;
                const nextHeight = updatedContainer.scrollHeight;
                updatedContainer.scrollTop = previousTop + (nextHeight - previousHeight);
                refreshHistoryViewportFlags();
            });
        } catch {
            // ignore loading errors
        } finally {
            isLoadingOlderRef.current = false;
            setIsLoadingOlder(false);
        }
    };

    const handleHistoryScroll = () => {
        const container = scrollRef.current;
        if (!container) return;
        refreshHistoryViewportFlags();
        if (container.scrollTop > TOP_LOAD_THRESHOLD_PX) return;
        if (!historyHasMore || !historyNextCursor) return;

        const now = Date.now();
        if (now - lastAutoOlderLoadAtRef.current < AUTO_LOAD_COOLDOWN_MS) return;
        lastAutoOlderLoadAtRef.current = now;
        void handleLoadOlder();
    };

    const showOlderControl = history.length > 0 && (isLoadingOlder || (isHistoryScrollable && isHistoryNearTop));

    useEffect(() => {
        if (!requestCompletions.length) return;
        const pendingRequestIds = pendingRequestIdsRef.current;
        const consumedCompletionIds = consumedCompletionIdsRef.current;
        const messagesToAppend: ChatMessage[] = [];
        const completedRequestIds = new Set<string>();

        for (const completion of requestCompletions) {
            if (consumedCompletionIds.has(completion.id)) continue;
            consumedCompletionIds.add(completion.id);
            if (!pendingRequestIds.has(completion.requestId)) continue;
            pendingRequestIds.delete(completion.requestId);
            completedRequestIds.add(completion.requestId);

            const text = completion.status === 'failed'
                ? `Fallo la solicitud: ${completion.text}`
                : completion.text;
            messagesToAppend.push({
                id: `${completion.id}:final`,
                role: 'system',
                text,
                createdAt: Date.now(),
                requestId: completion.requestId,
                status: completion.status,
                typewriter: true,
            });
        }

        if (messagesToAppend.length > 0) {
            shouldAutoScrollToBottomRef.current = true;
            setHistory((current) => [...current, ...messagesToAppend]);
        }
        if (completedRequestIds.size > 0) {
            setPendingRequests((current) =>
                current.filter((item) => !completedRequestIds.has(item.requestId))
            );
        }
    }, [requestCompletions]);

    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if (isEditableElement(e.target)) {
                if (e.key === 'Escape') {
                    setIsVisible(false);
                    setInput('');
                    setMentionIndex(-1);
                    setMentionType(null);
                }
                return;
            }

            const isPrintableChar = e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey;

            // Open on any char if not visible, except modifiers
            if (!isVisible && isPrintableChar) {
                setIsVisible(true);
                setTimeout(() => {
                    if (inputRef.current) {
                        inputRef.current.focus();
                        setInput(e.key);
                        updateMentionStateFromValue(e.key);
                    }
                }, 10);
                return;
            }

            if (isVisible && e.key === 'Escape') {
                setIsVisible(false);
                setInput('');
                setMentionIndex(-1);
                setMentionType(null);
                return;
            }

            // If chat is open but input lost focus (e.g. clicked history), typing should continue in input.
            if (isVisible && isPrintableChar) {
                e.preventDefault();
                setInput((current) => {
                    const next = `${current}${e.key}`;
                    updateMentionStateFromValue(next);
                    return next;
                });
                setTimeout(() => {
                    const inputEl = inputRef.current;
                    if (!inputEl) return;
                    inputEl.focus();
                    const cursorPos = inputEl.value.length;
                    inputEl.setSelectionRange(cursorPos, cursorPos);
                }, 0);
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [isVisible, updateMentionStateFromValue]);

    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const val = e.target.value;
        setInput(val);
        updateMentionStateFromValue(val);
    };

    const handleInputKeyDown = (e: React.KeyboardEvent) => {
        if (mentionIndex >= 0 && filteredItems.length > 0) {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                setSelectedIndex(s => (s + 1) % filteredItems.length);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                setSelectedIndex(s => (s - 1 + filteredItems.length) % filteredItems.length);
            } else if (e.key === 'Enter' || e.key === 'Tab') {
                e.preventDefault();
                const selected = filteredItems[selectedIndex].name;
                const bef = input.slice(0, mentionIndex);
                const aft = input.slice(mentionIndex + 1 + mentionQuery.length);
                const prefix = mentionType === 'agent' ? '@' : '#';
                const nextInput = bef + prefix + selected + ' ' + aft;
                setInput(nextInput);
                setMentionIndex(-1);
                setMentionType(null);

                setTimeout(() => {
                    if (inputRef.current) {
                        inputRef.current.focus();
                        inputRef.current.setSelectionRange(nextInput.length, nextInput.length);
                    }
                }, 0);
            }
        }
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (mentionIndex >= 0 && filteredItems.length > 0) {
            return; // intercept enter if menu open
        }

        if (!input.trim() || isSending) return;
        setIsSending(true);

        const userMsg = input.trim();
        setInput('');
        setMentionIndex(-1);
        setMentionType(null);

        const now = Date.now();
        shouldAutoScrollToBottomRef.current = true;
        setHistory(h => [...h, { id: now.toString(), role: 'user', text: userMsg, createdAt: now }]);

        const response = await onSendMessage(userMsg, sessionId || undefined);
        if (response?.sessionId && response.sessionId !== sessionId) {
            suppressHistoryReloadRef.current = true;
            setSessionId(response.sessionId);
        }
        const responseText = response?.text || 'Instruction received and routed.';
        const queuedRequestId = response?.queued && response?.requestId ? response.requestId : undefined;
        if (queuedRequestId) {
            pendingRequestIdsRef.current.add(queuedRequestId);
            setPendingRequests((current) => {
                if (current.some((item) => item.requestId === queuedRequestId)) {
                    return current;
                }
                return [
                    ...current,
                    {
                        requestId: queuedRequestId,
                        prompt: userMsg,
                        createdAt: Date.now(),
                    },
                ];
            });
        }

        const responseNow = Date.now();
        shouldAutoScrollToBottomRef.current = true;
        setHistory(h => [
            ...h,
            {
                id: (responseNow + 1).toString(),
                role: 'system',
                text: responseText,
                createdAt: responseNow,
                status: response?.queued ? 'pending' : 'completed',
                typewriter: true,
                ...(response?.requestId ? { requestId: response.requestId } : {}),
            },
        ]);
        setIsSending(false);
    };

    const handleTypewriterComplete = (messageId: string) => {
        setHistory((current) =>
            current.map((message) =>
                message.id === messageId ? { ...message, typewriter: false } : message
            )
        );
    };

    return (
        <AnimatePresence>
            {isVisible && (
                <motion.div
                    className="chat-overlay-backdrop flex flex-col items-center p-4"
                    style={{
                        position: 'fixed',
                        inset: 0,
                        zIndex: 1000,
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        justifyContent: 'flex-end',
                        padding: '24px 24px 56px',
                    }}
                    initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                    transition={{ duration: 0.2 }}
                    onClick={(e) => {
                        if (e.target === e.currentTarget) {
                            setIsVisible(false);
                        }
                    }}
                >
                    {/* Main Container that slides from bottom */}
                    <motion.div
                        style={{ width: '100%', maxWidth: '820px', maxHeight: '90vh', display: 'flex', flexDirection: 'column' }}
                        initial={{ y: 50, scale: 0.95 }} animate={{ y: 0, scale: 1 }} exit={{ y: 50, scale: 0.95, opacity: 0 }}
                    >
                        {/* Chat History Log */}
                        {history.length > 0 && (
                            <div style={{ display: 'flex', flexDirection: 'column', marginBottom: '1rem', gap: '0.55rem' }}>
                                {showOlderControl && (
                                    <div style={{ display: 'flex', justifyContent: 'center', minHeight: '1.9rem' }}>
                                        {(historyHasMore || isLoadingOlder) ? (
                                            <button
                                                type="button"
                                                onClick={() => {
                                                    if (!isLoadingOlder) {
                                                        void handleLoadOlder();
                                                    }
                                                }}
                                                disabled={isLoadingOlder}
                                                style={{
                                                    padding: '0.36rem 0.9rem',
                                                    borderRadius: '999px',
                                                    border: '1px solid rgba(136, 154, 235, 0.32)',
                                                    background: isLoadingOlder ? 'rgba(54, 66, 114, 0.45)' : 'rgba(31, 40, 76, 0.42)',
                                                    color: 'rgba(223, 230, 255, 0.9)',
                                                    fontSize: '0.69rem',
                                                    letterSpacing: '0.03em',
                                                    textTransform: 'uppercase',
                                                    cursor: isLoadingOlder ? 'default' : 'pointer',
                                                    opacity: isLoadingOlder ? 0.85 : 1,
                                                    transition: 'all 0.16s ease',
                                                }}
                                            >
                                                {isLoadingOlder ? 'Loading older messages...' : 'Load older messages'}
                                            </button>
                                        ) : (
                                            <div
                                                style={{
                                                    padding: '0.32rem 0.72rem',
                                                    borderRadius: '999px',
                                                    border: '1px solid rgba(112, 127, 188, 0.24)',
                                                    background: 'rgba(18, 26, 52, 0.3)',
                                                    color: 'rgba(173, 184, 224, 0.72)',
                                                    fontSize: '0.66rem',
                                                    letterSpacing: '0.03em',
                                                    textTransform: 'uppercase',
                                                    userSelect: 'none',
                                                }}
                                            >
                                                No older messages
                                            </div>
                                        )}
                                    </div>
                                )}

                                <div
                                    ref={scrollRef}
                                    onScroll={handleHistoryScroll}
                                    style={{
                                        maxHeight: '58vh', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '1rem',
                                        padding: '1rem', maskImage: 'linear-gradient(to bottom, transparent 0%, black 15%)',
                                        WebkitMaskImage: 'linear-gradient(to bottom, transparent 0%, black 15%)'
                                    }}
                                >
                                {history.map((msgAction) => (
                                    <div key={msgAction.id} style={{ display: 'flex', justifyContent: msgAction.role === 'user' ? 'flex-end' : 'flex-start' }}>
                                        <div style={{
                                            maxWidth: '80%', padding: '12px 20px', borderRadius: '16px',
                                            fontFamily: msgAction.role === 'system' ? 'monospace' : 'Outfit, sans-serif',
                                            fontSize: msgAction.role === 'system' ? '0.9rem' : '1.05rem',
                                            background: msgAction.role === 'user' ? 'rgba(99, 102, 241, 0.15)' : 'rgba(15, 17, 26, 0.8)',
                                            border: msgAction.role === 'user'
                                                ? '1px solid rgba(99, 102, 241, 0.3)'
                                                : msgAction.status === 'failed'
                                                    ? '1px solid rgba(176, 114, 138, 0.35)'
                                                    : msgAction.status === 'pending'
                                                        ? '1px solid rgba(116, 149, 220, 0.34)'
                                                        : '1px solid rgba(124, 142, 230, 0.3)',
                                            color: msgAction.role === 'user'
                                                ? '#fff'
                                                : msgAction.status === 'failed'
                                                    ? '#f2d0da'
                                                    : msgAction.status === 'pending'
                                                        ? '#d2def9'
                                                        : '#dbe4ff',
                                            borderBottomRightRadius: msgAction.role === 'user' ? '4px' : '16px',
                                            borderBottomLeftRadius: msgAction.role === 'system' ? '4px' : '16px',
                                            boxShadow: '0 4px 15px rgba(0,0,0,0.3)',
                                            lineHeight: 1.5,
                                            backdropFilter: 'blur(8px)',
                                            overflowWrap: 'anywhere',
                                            wordBreak: 'break-word',
                                            whiteSpace: 'pre-wrap',
                                            minWidth: 0,
                                        }}>
                                            <div style={{ overflowWrap: 'anywhere', wordBreak: 'break-word' }}>
                                                {msgAction.role === 'system' && <span style={{ opacity: 0.5, marginRight: '8px' }}>&gt;</span>}
                                                {msgAction.typewriter
                                                    ? <TypewriterText text={msgAction.text} onComplete={() => handleTypewriterComplete(msgAction.id)} />
                                                    : msgAction.role === 'system'
                                                        ? <ChatMarkdown content={msgAction.text} />
                                                        : msgAction.text}
                                            </div>
                                            <div
                                                style={{
                                                    marginTop: '8px',
                                                    fontSize: '0.68rem',
                                                    opacity: 0.55,
                                                    textAlign: msgAction.role === 'user' ? 'right' : 'left',
                                                    fontFamily: 'Inter, sans-serif',
                                                }}
                                            >
                                                {formatMessageTime(msgAction.createdAt)}
                                                {msgAction.status === 'pending' ? ' | in progress' : ''}
                                            </div>
                                        </div>
                                    </div>
                                ))}
                                </div>
                            </div>
                        )}

                        {/* Input Form */}
                        <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                            <form
                                className="chat-input-container relative"
                                style={{ position: 'relative', width: '100%', padding: '12px', background: 'rgba(15, 17, 26, 0.85)', backdropFilter: 'blur(24px)' }}
                                onSubmit={handleSubmit}
                            >
                                <Command size={24} color="var(--accent-primary)" style={{ flexShrink: 0 }} />
                                <input
                                    ref={inputRef}
                                    type="text"
                                    className="chat-input flex-1"
                                    value={input}
                                    onChange={handleChange}
                                    onKeyDown={handleInputKeyDown}
                                    placeholder="Ask the orchestrator, mention #project..."
                                    disabled={isSending}
                                    style={{ width: '100%' }}
                                />
                                {isSending && (
                                    <motion.div
                                        animate={{ rotate: 360 }}
                                        transition={{ repeat: Infinity, duration: 1, ease: 'linear' }}
                                        style={{ width: 20, height: 20, border: '2px solid rgba(255,255,255,0.2)', borderTopColor: 'var(--accent-primary)', borderRadius: '50%', flexShrink: 0 }}
                                    />
                                )}

                                {/* Autocomplete Menu */}
                                <AnimatePresence>
                                    {mentionIndex >= 0 && filteredItems.length > 0 && (
                                        <motion.div
                                            initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                                            style={{
                                                position: 'absolute', bottom: 'calc(100% + 16px)', left: '1rem',
                                                background: 'rgba(15, 17, 26, 0.95)', backdropFilter: 'blur(16px)',
                                                border: '1px solid var(--border-card)', borderRadius: '16px',
                                                padding: '8px', boxShadow: '0 10px 40px rgba(0,0,0,0.8)',
                                                display: 'flex', flexDirection: 'column', gap: '4px', minWidth: '240px', zIndex: 100
                                            }}
                                        >
                                            {filteredItems.map((item, i) => (
                                                <div
                                                    key={item.name}
                                                    style={{
                                                        padding: '10px 12px', display: 'flex', alignItems: 'center', gap: '12px',
                                                        borderRadius: '10px', transition: 'background 0.1s', cursor: 'pointer',
                                                        background: selectedIndex === i ? 'var(--accent-glow)' : 'transparent',
                                                        border: selectedIndex === i ? '1px solid rgba(99, 102, 241, 0.3)' : '1px solid transparent'
                                                    }}
                                                    onClick={() => {
                                                        const bef = input.slice(0, mentionIndex);
                                                        const aft = input.slice(mentionIndex + 1 + mentionQuery.length);
                                                        const prefix = mentionType === 'agent' ? '@' : '#';
                                                        setInput(bef + prefix + item.name + ' ' + aft);
                                                        setMentionIndex(-1);
                                                        setMentionType(null);
                                                        if (inputRef.current) inputRef.current.focus();
                                                    }}
                                                >
                                                    {mentionType === 'project' ? <AgentAvatar name={item.name} size={28} /> : null}
                                                    <div style={{ display: 'flex', flexDirection: 'column' }}>
                                                        <span style={{ fontSize: '1rem', fontWeight: 500, color: '#fff', fontFamily: 'Outfit, sans-serif' }}>{item.name}</span>
                                                        {mentionType === 'agent' && <span style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>{(item as any).role}</span>}
                                                    </div>
                                                </div>
                                            ))}
                                            <div style={{ fontSize: '0.7rem', color: 'var(--text-tertiary)', textAlign: 'center', marginTop: '6px', paddingTop: '6px', borderTop: '1px solid rgba(255,255,255,0.05)' }}>
                                                Use arrow keys to navigate, Enter to select
                                            </div>
                                        </motion.div>
                                    )}
                                </AnimatePresence>

                            </form>

                            <div style={{ fontSize: '0.74rem', color: 'var(--text-tertiary)', padding: '0 6px' }}>
                                Shortcuts: <strong>Esc</strong> close, <strong>Enter</strong> send, <strong>#</strong> project mention.
                            </div>

                            {pendingRequests.length > 0 && (
                                <div
                                    style={{
                                        fontSize: '0.76rem',
                                        color: 'var(--text-secondary)',
                                        padding: '8px 10px',
                                        borderRadius: '10px',
                                        border: '1px solid rgba(116, 149, 220, 0.28)',
                                        background: 'rgba(26, 35, 68, 0.36)',
                                    }}
                                >
                                    {pendingRequests.length === 1
                                        ? `1 request in progress: ${truncateText(pendingRequests[0].prompt, 72)}`
                                        : `${pendingRequests.length} requests in progress`}
                                </div>
                            )}
                        </div>
                    </motion.div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}

// Compact markdown renderer for chat bubbles
const chatMarkdownComponents = {
    h1: ({ children, ...props }: any) => <div style={{ fontSize: '1.05em', fontWeight: 700, margin: '0.4em 0 0.2em' }} {...props}>{children}</div>,
    h2: ({ children, ...props }: any) => <div style={{ fontSize: '1em', fontWeight: 700, margin: '0.4em 0 0.2em' }} {...props}>{children}</div>,
    h3: ({ children, ...props }: any) => <div style={{ fontSize: '0.95em', fontWeight: 600, margin: '0.3em 0 0.15em' }} {...props}>{children}</div>,
    h4: ({ children, ...props }: any) => <div style={{ fontSize: '0.9em', fontWeight: 600, margin: '0.25em 0 0.1em' }} {...props}>{children}</div>,
    p: ({ children, ...props }: any) => <div style={{ margin: '0.3em 0' }} {...props}>{children}</div>,
    ul: ({ children, ...props }: any) => <ul style={{ margin: '0.25em 0', paddingLeft: '1.3em' }} {...props}>{children}</ul>,
    ol: ({ children, ...props }: any) => <ol style={{ margin: '0.25em 0', paddingLeft: '1.3em' }} {...props}>{children}</ol>,
    li: ({ children, ...props }: any) => <li style={{ margin: '0.1em 0' }} {...props}>{children}</li>,
    strong: ({ children, ...props }: any) => <strong style={{ fontWeight: 600, color: '#fff' }} {...props}>{children}</strong>,
    em: ({ children, ...props }: any) => <em style={{ fontStyle: 'italic', opacity: 0.9 }} {...props}>{children}</em>,
    code: ({ inline, children, ...props }: any) =>
        inline !== false && !props.className ? (
            <code style={{ background: 'rgba(255,255,255,0.08)', padding: '0.1em 0.35em', borderRadius: '4px', fontSize: '0.88em', fontFamily: 'monospace' }} {...props}>{children}</code>
        ) : (
            <pre style={{ background: 'rgba(0,0,0,0.3)', padding: '0.5em 0.7em', borderRadius: '6px', fontSize: '0.85em', overflowX: 'auto', margin: '0.3em 0' }}><code {...props}>{children}</code></pre>
        ),
    a: ({ children, ...props }: any) => <a style={{ color: 'var(--accent-primary)', textDecoration: 'underline' }} target="_blank" rel="noopener noreferrer" {...props}>{children}</a>,
    blockquote: ({ children, ...props }: any) => <blockquote style={{ borderLeft: '3px solid rgba(99,102,241,0.4)', paddingLeft: '0.7em', margin: '0.3em 0', opacity: 0.85 }} {...props}>{children}</blockquote>,
    table: ({ children, ...props }: any) => <div style={{ overflowX: 'auto', margin: '0.3em 0' }}><table style={{ borderCollapse: 'collapse', fontSize: '0.88em', width: '100%' }} {...props}>{children}</table></div>,
    th: ({ children, ...props }: any) => <th style={{ border: '1px solid rgba(255,255,255,0.12)', padding: '0.3em 0.5em', fontWeight: 600, textAlign: 'left' }} {...props}>{children}</th>,
    td: ({ children, ...props }: any) => <td style={{ border: '1px solid rgba(255,255,255,0.08)', padding: '0.3em 0.5em' }} {...props}>{children}</td>,
    hr: () => <hr style={{ border: 'none', borderTop: '1px solid rgba(255,255,255,0.1)', margin: '0.5em 0' }} />,
};

function ChatMarkdown({ content }: { content: string }) {
    return (
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={chatMarkdownComponents}>
            {content}
        </ReactMarkdown>
    );
}

// Helper component for typewriter effect on new system messages
function TypewriterText({ text, onComplete }: { text: string; onComplete: () => void }) {
    const [displayed, setDisplayed] = useState("");
    const onCompleteRef = useRef(onComplete);
    onCompleteRef.current = onComplete;

    useEffect(() => {
        let i = 0;
        setDisplayed("");
        const timer = setInterval(() => {
            setDisplayed(() => {
                const next = text.slice(0, i + 1);
                i++;
                if (i >= text.length) {
                    clearInterval(timer);
                    onCompleteRef.current();
                }
                return next;
            });
        }, 30);
        return () => clearInterval(timer);
    }, [text]);

    const isComplete = displayed.length >= text.length;

    return (
        <>
            {isComplete
                ? <ChatMarkdown content={displayed} />
                : displayed
            }
            {!isComplete && (
                <motion.span animate={{ opacity: [1, 0, 1] }} transition={{ repeat: Infinity, duration: 0.8 }} style={{ display: 'inline-block', width: '8px', height: '14px', background: 'var(--accent-primary)', marginLeft: '4px', verticalAlign: 'middle' }} />
            )}
        </>
    );
}
