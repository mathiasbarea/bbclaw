import React, { useState } from 'react';


export function SpotlightCard({
    children,
    className = '',
    activeColor = 'rgba(99, 102, 241, 0.15)', // Indigo glow
    onClick,
    ariaPressed,
    includeBaseClass = true,
}: {
    children: React.ReactNode;
    className?: string;
    activeColor?: string;
    onClick?: () => void;
    ariaPressed?: boolean;
    includeBaseClass?: boolean;
}) {
    const [position, setPosition] = useState({ x: 0, y: 0 });
    const [opacity, setOpacity] = useState(0);

    const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
        const rect = e.currentTarget.getBoundingClientRect();
        setPosition({ x: e.clientX - rect.left, y: e.clientY - rect.top });
    };

    const handleMouseEnter = () => setOpacity(1);
    const handleMouseLeave = () => setOpacity(0);

    const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
        if (!onClick) return;
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onClick();
        }
    };

    const baseClassName = includeBaseClass ? 'glass-item ' : '';
    return (
        <div
            className={`${baseClassName}relative overflow-hidden transition-all duration-300 ${className}`}
            onMouseMove={handleMouseMove}
            onMouseEnter={handleMouseEnter}
            onMouseLeave={handleMouseLeave}
            onClick={onClick}
            onKeyDown={handleKeyDown}
            role={onClick ? 'button' : undefined}
            tabIndex={onClick ? 0 : undefined}
            aria-pressed={typeof ariaPressed === 'boolean' ? ariaPressed : undefined}
            style={{ position: 'relative', cursor: onClick ? 'pointer' : 'default' }}
        >
            <div
                style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    width: '100%',
                    height: '100%',
                    pointerEvents: 'none',
                    opacity,
                    background: `radial-gradient(400px circle at ${position.x}px ${position.y}px, ${activeColor}, transparent 40%)`,
                    transition: 'opacity 0.3s ease',
                    zIndex: 0,
                }}
            />
            <div style={{ position: 'relative', zIndex: 1 }}>
                {children}
            </div>
        </div>
    );
}
