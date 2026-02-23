import { useCallback, useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';

type OrbMood = 'awake' | 'pre_sleep' | 'sleeping' | 'wake_surprised';

interface SleepGlyph {
    id: number;
    char: 'Z' | 'z';
    offsetX: number;
    driftY: number;
    durationMs: number;
    sizePx: number;
}

const IDLE_THRESHOLD_MS = 10_000;
const SLEEP_COOLDOWN_MS = 20_000;
const PRE_SLEEP_PHASE_ONE_MS = 500;
const PRE_SLEEP_PHASE_TWO_MS = 700;
const PRE_SLEEP_PHASE_THREE_MS = 600;
const PRE_SLEEP_TOTAL_MS = PRE_SLEEP_PHASE_ONE_MS + PRE_SLEEP_PHASE_TWO_MS + PRE_SLEEP_PHASE_THREE_MS;
const WAKE_SURPRISE_MS = 650;

export function Orb({ pendingCount, activeAgents }: { pendingCount: number; activeAgents: number }) {
    const orbRef = useRef<HTMLDivElement>(null);
    const [eyeOffset, setEyeOffset] = useState({ x: 0, y: 0 });
    const [blinkScale, setBlinkScale] = useState(1);
    const [isUserIdle, setIsUserIdle] = useState(false);
    const [orbMood, setOrbMood] = useState<OrbMood>('awake');
    const [sleepGlyphs, setSleepGlyphs] = useState<SleepGlyph[]>([]);
    const [preSleepPhase, setPreSleepPhase] = useState(0);
    const [cooldownTick, setCooldownTick] = useState(0);

    const orbMoodRef = useRef<OrbMood>('awake');
    const activeAgentsRef = useRef(activeAgents);
    const isUserIdleRef = useRef(false);
    const lastActivityAtRef = useRef(Date.now());
    const sleepCooldownUntilRef = useRef(0);
    const preSleepTimeoutsRef = useRef<Array<ReturnType<typeof setTimeout>>>([]);
    const wakeTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const cooldownTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const glyphCounterRef = useRef(0);

    useEffect(() => {
        orbMoodRef.current = orbMood;
    }, [orbMood]);

    useEffect(() => {
        activeAgentsRef.current = activeAgents;
    }, [activeAgents]);

    const clearPreSleepTimers = useCallback(() => {
        if (preSleepTimeoutsRef.current.length > 0) {
            for (const timer of preSleepTimeoutsRef.current) clearTimeout(timer);
            preSleepTimeoutsRef.current = [];
        }
    }, []);

    const clearWakeTimer = useCallback(() => {
        if (wakeTimeoutRef.current) {
            clearTimeout(wakeTimeoutRef.current);
            wakeTimeoutRef.current = null;
        }
    }, []);

    const clearCooldownTimer = useCallback(() => {
        if (cooldownTimeoutRef.current) {
            clearTimeout(cooldownTimeoutRef.current);
            cooldownTimeoutRef.current = null;
        }
    }, []);

    const scheduleCooldownRecheck = useCallback(() => {
        clearCooldownTimer();
        const remainingMs = sleepCooldownUntilRef.current - Date.now();
        if (remainingMs <= 0) {
            setCooldownTick((current) => current + 1);
            return;
        }

        cooldownTimeoutRef.current = setTimeout(() => {
            cooldownTimeoutRef.current = null;
            setCooldownTick((current) => current + 1);
        }, remainingMs);
    }, [clearCooldownTimer]);

    const triggerWakeSurprised = useCallback(() => {
        if (!(orbMoodRef.current === 'sleeping' || orbMoodRef.current === 'pre_sleep')) return;

        sleepCooldownUntilRef.current = Date.now() + SLEEP_COOLDOWN_MS;
        scheduleCooldownRecheck();
        clearPreSleepTimers();
        clearWakeTimer();
        setPreSleepPhase(0);
        setEyeOffset({ x: 0, y: 0 });
        setOrbMood('wake_surprised');

        wakeTimeoutRef.current = setTimeout(() => {
            wakeTimeoutRef.current = null;
            setOrbMood('awake');
        }, WAKE_SURPRISE_MS);
    }, [clearPreSleepTimers, clearWakeTimer, scheduleCooldownRecheck]);

    useEffect(() => {
        if (orbMood === 'sleeping' || orbMood === 'pre_sleep') {
            setEyeOffset({ x: 0, y: 0 });
        }
    }, [orbMood]);

    // Mouse tracking + idle detection + wake on activity.
    useEffect(() => {
        const setIdleState = (idle: boolean) => {
            if (isUserIdleRef.current === idle) return;
            isUserIdleRef.current = idle;
            setIsUserIdle(idle);
        };

        const registerActivity = () => {
            lastActivityAtRef.current = Date.now();
            setIdleState(false);
            triggerWakeSurprised();
        };

        const handleMouseMove = (e: MouseEvent) => {
            registerActivity();
            if (!orbRef.current) return;
            if (orbMoodRef.current === 'sleeping' || orbMoodRef.current === 'pre_sleep') return;

            const rect = orbRef.current.getBoundingClientRect();
            const orbCenterX = rect.left + rect.width / 2;
            const orbCenterY = rect.top + rect.height / 2;

            const dx = e.clientX - orbCenterX;
            const dy = e.clientY - orbCenterY;
            const distance = Math.sqrt(dx * dx + dy * dy);
            const maxOffset = 20;
            const moveX = (dx / (distance || 1)) * Math.min(distance / 8, maxOffset);
            const moveY = (dy / (distance || 1)) * Math.min(distance / 8, maxOffset);

            setEyeOffset({ x: moveX, y: moveY });
        };

        const handleActivity = () => {
            registerActivity();
        };

        window.addEventListener('mousemove', handleMouseMove, { passive: true });
        window.addEventListener('mousedown', handleActivity, { passive: true });
        window.addEventListener('keydown', handleActivity);
        window.addEventListener('touchstart', handleActivity, { passive: true });
        window.addEventListener('scroll', handleActivity, { passive: true });
        window.addEventListener('pointerdown', handleActivity, { passive: true });

        const idleInterval = setInterval(() => {
            const idle = Date.now() - lastActivityAtRef.current >= IDLE_THRESHOLD_MS;
            setIdleState(idle);
        }, 350);

        return () => {
            clearInterval(idleInterval);
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('mousedown', handleActivity);
            window.removeEventListener('keydown', handleActivity);
            window.removeEventListener('touchstart', handleActivity);
            window.removeEventListener('scroll', handleActivity);
            window.removeEventListener('pointerdown', handleActivity);
        };
    }, [triggerWakeSurprised]);

    const canContinueSleepSequence = useCallback(() => {
        return isUserIdleRef.current && activeAgentsRef.current === 0 && Date.now() >= sleepCooldownUntilRef.current;
    }, []);

    // Sleep transition logic (awake -> pre_sleep -> sleeping).
    useEffect(() => {
        if (activeAgents > 0) {
            triggerWakeSurprised();
            return;
        }

        const canSleepNow = isUserIdle && Date.now() >= sleepCooldownUntilRef.current;
        if (!canSleepNow) {
            clearPreSleepTimers();
            if (orbMood === 'pre_sleep') {
                setPreSleepPhase(0);
                setOrbMood('awake');
            }
            return;
        }

        if (orbMood !== 'awake' || preSleepTimeoutsRef.current.length > 0) return;

        setOrbMood('pre_sleep');
        setPreSleepPhase(1);

        const toPhaseTwoTimer = setTimeout(() => {
            if (canContinueSleepSequence() && orbMoodRef.current === 'pre_sleep') {
                setPreSleepPhase(2);
                return;
            }

            if (orbMoodRef.current === 'pre_sleep') {
                clearPreSleepTimers();
                setPreSleepPhase(0);
                setOrbMood('awake');
            }
        }, PRE_SLEEP_PHASE_ONE_MS);

        const toPhaseThreeTimer = setTimeout(() => {
            if (canContinueSleepSequence() && orbMoodRef.current === 'pre_sleep') {
                setPreSleepPhase(3);
                return;
            }

            if (orbMoodRef.current === 'pre_sleep') {
                clearPreSleepTimers();
                setPreSleepPhase(0);
                setOrbMood('awake');
            }
        }, PRE_SLEEP_PHASE_ONE_MS + PRE_SLEEP_PHASE_TWO_MS);

        const toSleepingTimer = setTimeout(() => {
            preSleepTimeoutsRef.current = [];
            if (canContinueSleepSequence() && orbMoodRef.current === 'pre_sleep') {
                setPreSleepPhase(0);
                setOrbMood('sleeping');
                return;
            }

            if (orbMoodRef.current === 'pre_sleep') {
                clearPreSleepTimers();
                setPreSleepPhase(0);
                setOrbMood('awake');
            }
        }, PRE_SLEEP_TOTAL_MS);

        preSleepTimeoutsRef.current = [toPhaseTwoTimer, toPhaseThreeTimer, toSleepingTimer];
    }, [activeAgents, isUserIdle, orbMood, cooldownTick, canContinueSleepSequence, clearPreSleepTimers, triggerWakeSurprised]);

    // Natural blinking only while awake.
    useEffect(() => {
        setBlinkScale(1);
        if (orbMood !== 'awake') return;

        let disposed = false;
        const timers: Array<ReturnType<typeof setTimeout>> = [];
        const schedule = (fn: () => void, delayMs: number) => {
            const timer = setTimeout(fn, delayMs);
            timers.push(timer);
        };

        const blink = () => {
            if (disposed) return;
            setBlinkScale(0.12);
            schedule(() => {
                if (!disposed) setBlinkScale(1);
            }, 110);

            if (Math.random() > 0.82) {
                schedule(() => {
                    if (disposed) return;
                    setBlinkScale(0.12);
                    schedule(() => {
                        if (!disposed) setBlinkScale(1);
                    }, 105);
                }, 230);
            }

            schedule(blink, Math.random() * 4500 + 2500);
        };

        schedule(blink, 1800);
        return () => {
            disposed = true;
            for (const timer of timers) clearTimeout(timer);
        };
    }, [orbMood]);

    // Sleeping glyphs (ZzZ) while sleeping.
    useEffect(() => {
        if (orbMood !== 'sleeping') {
            setSleepGlyphs([]);
            return;
        }

        let disposed = false;
        let spawnTimer: ReturnType<typeof setTimeout> | null = null;
        const removeTimers: Array<ReturnType<typeof setTimeout>> = [];

        const spawnGlyph = (isInitial = false) => {
            const id = ++glyphCounterRef.current;
            const glyph: SleepGlyph = {
                id,
                char: isInitial ? 'z' : Math.random() > 0.45 ? 'Z' : 'z',
                offsetX: isInitial ? -4 : Math.round(Math.random() * 18 - 9),
                driftY: isInitial ? 38 : Math.round(42 + Math.random() * 30),
                durationMs: isInitial ? 1500 : Math.round(1600 + Math.random() * 700),
                sizePx: isInitial ? 14 : Math.round(16 + Math.random() * 5),
            };

            setSleepGlyphs((current) => {
                const next = [...current, glyph];
                return next.length > 3 ? next.slice(next.length - 3) : next;
            });

            const removeTimer = setTimeout(() => {
                setSleepGlyphs((current) => current.filter((item) => item.id !== id));
            }, glyph.durationMs + 220);
            removeTimers.push(removeTimer);
        };

        const loop = () => {
            if (disposed) return;
            spawnGlyph();
            spawnTimer = setTimeout(loop, 900 + Math.random() * 500);
        };

        spawnGlyph(true);
        spawnTimer = setTimeout(loop, 900 + Math.random() * 500);
        return () => {
            disposed = true;
            if (spawnTimer) clearTimeout(spawnTimer);
            for (const timer of removeTimers) clearTimeout(timer);
            setSleepGlyphs([]);
        };
    }, [orbMood]);

    useEffect(() => {
        return () => {
            clearPreSleepTimers();
            clearWakeTimer();
            clearCooldownTimer();
        };
    }, [clearPreSleepTimers, clearWakeTimer, clearCooldownTimer]);

    const preSleepStage = preSleepPhase === 0 ? 1 : preSleepPhase;
    const preSleepEyeScaleY = 0.76;
    const baseEyeScaleY =
        orbMood === 'sleeping' ? 0.24 : orbMood === 'pre_sleep' ? preSleepEyeScaleY : orbMood === 'wake_surprised' ? 1.28 : 1;
    const baseEyeScaleX = orbMood === 'wake_surprised' ? 1.12 : orbMood === 'sleeping' ? 1.06 : 1;
    const eyeScaleY = Math.max(0.1, baseEyeScaleY * blinkScale);
    const eyeGlow =
        orbMood === 'sleeping'
            ? '0 0 14px rgba(255,255,255,0.45)'
            : orbMood === 'wake_surprised'
                ? '0 0 26px rgba(255,255,255,1)'
                : '0 0 20px rgba(255,255,255,0.9)';
    const eyeOpacity = orbMood === 'sleeping' ? 0.86 : orbMood === 'pre_sleep' ? (preSleepStage === 1 ? 0.98 : preSleepStage === 2 ? 0.93 : 0.89) : 1;

    const gradientDuration = orbMood === 'sleeping' ? 42 : orbMood === 'pre_sleep' ? 28 : 20;
    const gradientOpacity =
        orbMood === 'sleeping'
            ? 0.34
            : orbMood === 'pre_sleep'
                ? preSleepStage === 1
                    ? 0.46
                    : preSleepStage === 2
                        ? 0.4
                        : 0.36
                : orbMood === 'wake_surprised'
                    ? 0.6
                    : 0.5;

    const preSleepAnimate =
        preSleepStage === 1
            ? { scale: [1, 0.997, 1], y: 0, rotate: 0 }
            : preSleepStage === 2
                ? { scale: [1, 0.992, 1], y: 0, rotate: 0 }
                : { scale: [1, 0.989, 1], y: 0, rotate: 0 };

    const preSleepTransition =
        preSleepStage === 1
            ? { duration: PRE_SLEEP_PHASE_ONE_MS / 1000, ease: 'easeInOut' as const }
            : preSleepStage === 2
                ? { duration: PRE_SLEEP_PHASE_TWO_MS / 1000, ease: 'easeInOut' as const }
                : { duration: PRE_SLEEP_PHASE_THREE_MS / 1000, ease: 'easeInOut' as const };

    const centralAnimate =
        orbMood === 'sleeping'
            ? { scale: [1, 0.985, 1], y: [0, 2, 0], rotate: 0 }
            : orbMood === 'pre_sleep'
                ? preSleepAnimate
                : orbMood === 'wake_surprised'
                    ? { scale: [1, 1.04, 1], y: [0, -8, 0], rotate: [0, -2, 2, 0] }
                    : { scale: [1, 1.015, 1], y: 0, rotate: 0 };

    const centralTransition =
        orbMood === 'sleeping'
            ? { duration: 4.6, ease: 'easeInOut' as const, repeat: Infinity }
            : orbMood === 'pre_sleep'
                ? preSleepTransition
                : orbMood === 'wake_surprised'
                    ? { duration: 0.55, ease: 'easeOut' as const }
                    : { duration: 5.8, ease: 'easeInOut' as const, repeat: Infinity };

    return (
        <div style={{ position: 'relative', width: '280px', height: '280px' }} ref={orbRef}>
            {/* Animated Gradient Behind */}
            <motion.div
                style={{
                    position: 'absolute',
                    top: '-15px',
                    left: '-15px',
                    right: '-15px',
                    bottom: '-15px',
                    background: 'var(--orb-gradient)',
                    borderRadius: '50%',
                    filter: 'blur(35px)',
                }}
                animate={{ rotate: 360, opacity: gradientOpacity }}
                transition={{
                    rotate: { repeat: Infinity, duration: gradientDuration, ease: 'linear' },
                    opacity: { duration: 0.25, ease: 'easeInOut' },
                }}
            />

            <AnimatePresence>
                {orbMood === 'sleeping' &&
                    sleepGlyphs.map((glyph) => (
                        <motion.span
                            key={glyph.id}
                            style={{
                                position: 'absolute',
                                left: `calc(50% + ${glyph.offsetX}px)`,
                                top: '38px',
                                color: 'rgba(219, 228, 255, 0.95)',
                                fontFamily: 'Outfit, sans-serif',
                                fontSize: `${glyph.sizePx}px`,
                                fontWeight: 600,
                                textShadow: '0 0 10px rgba(99, 102, 241, 0.45)',
                                zIndex: 12,
                                pointerEvents: 'none',
                            }}
                            initial={{ opacity: 0, y: 0, x: 0, scale: 0.9 }}
                            animate={{
                                opacity: [0, 0.82, 0],
                                y: -glyph.driftY,
                                x: glyph.offsetX * 0.45,
                                scale: [0.9, 1.16],
                            }}
                            exit={{ opacity: 0 }}
                            transition={{ duration: glyph.durationMs / 1000, ease: 'easeOut' }}
                        >
                            {glyph.char}
                        </motion.span>
                    ))}
            </AnimatePresence>

            {/* Central Black Hole (The Orb) */}
            <motion.div
                animate={centralAnimate}
                transition={centralTransition}
                style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    right: 0,
                    bottom: 0,
                    background: 'radial-gradient(circle at 35% 35%, #1f2233 0%, #050507 80%)',
                    borderRadius: '50%',
                    boxShadow:
                        'inset -20px -20px 50px rgba(0,0,0,0.9), inset 10px 10px 25px rgba(255,255,255,0.06), 0 10px 40px rgba(0,0,0,0.5)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexDirection: 'column',
                }}
            >
                {/* The Eyes Container (moves with mouse) */}
                <motion.div
                    style={{ display: 'flex', gap: '14px', zIndex: 10 }}
                    animate={{ x: eyeOffset.x, y: eyeOffset.y }}
                    transition={{ type: 'spring', stiffness: 200, damping: 25, mass: 0.5 }}
                >
                    <div
                        style={{
                            width: 16,
                            height: 42,
                            background: '#fff',
                            borderRadius: '12px',
                            boxShadow: eyeGlow,
                            opacity: eyeOpacity,
                            transform: `scaleX(${baseEyeScaleX}) scaleY(${eyeScaleY})`,
                            transformOrigin: 'center',
                            transition: 'transform 180ms ease, box-shadow 220ms ease, opacity 180ms ease',
                        }}
                    />
                    <div
                        style={{
                            width: 16,
                            height: 42,
                            background: '#fff',
                            borderRadius: '12px',
                            boxShadow: eyeGlow,
                            opacity: eyeOpacity,
                            transform: `scaleX(${baseEyeScaleX}) scaleY(${eyeScaleY})`,
                            transformOrigin: 'center',
                            transition: 'transform 180ms ease, box-shadow 220ms ease, opacity 180ms ease',
                        }}
                    />
                </motion.div>

                {/* Notification Badge */}
                <AnimatePresence>
                    {pendingCount > 0 && (
                        <motion.div
                            style={{
                                position: 'absolute',
                                bottom: '-12px',
                                background: 'var(--accent-yellow)',
                                color: '#000',
                                padding: '6px 18px',
                                borderRadius: '24px',
                                fontSize: '0.9rem',
                                fontFamily: 'Outfit, sans-serif',
                                fontWeight: 600,
                                border: '3px solid var(--bg-deep)',
                                boxShadow: '0 4px 15px rgba(245, 158, 11, 0.5)',
                            }}
                            initial={{ scale: 0, y: 15 }}
                            animate={{ scale: 1, y: 0 }}
                            exit={{ scale: 0, y: 15 }}
                            transition={{ type: 'spring', stiffness: 500, damping: 25 }}
                        >
                            <motion.span
                                style={{ display: 'inline-block' }}
                                animate={{
                                    scale: [1, 1.03, 1],
                                    textShadow: [
                                        '0 0 0 rgba(245, 158, 11, 0)',
                                        '0 0 10px rgba(245, 158, 11, 0.35)',
                                        '0 0 0 rgba(245, 158, 11, 0)',
                                    ],
                                }}
                                transition={{ duration: 2.1, ease: 'easeInOut', repeat: Infinity }}
                            >
                                {pendingCount} pending
                            </motion.span>
                        </motion.div>
                    )}
                </AnimatePresence>
            </motion.div>
        </div>
    );
}
