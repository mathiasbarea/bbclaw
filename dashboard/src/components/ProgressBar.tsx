
import { motion } from 'framer-motion';

export function IndeterminateProgress() {
    return (
        <div style={{
            width: '100%',
            height: '3px',
            background: 'rgba(255,255,255,0.05)',
            overflow: 'hidden',
            borderRadius: '2px',
            marginTop: '12px',
            position: 'relative'
        }}>
            <motion.div
                style={{
                    height: '100%',
                    background: 'var(--accent-primary)',
                    width: '50%',
                    borderRadius: '2px',
                    boxShadow: '0 0 10px var(--accent-glow)'
                }}
                initial={{ x: '-100%' }}
                animate={{ x: '200%' }}
                transition={{
                    repeat: Infinity,
                    duration: 1.5,
                    ease: "linear"
                }}
            />
        </div>
    );
}
