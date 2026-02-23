

// Simplified seeded random based on string
const seededRandom = (seed: string) => {
    let h = 0;
    for (let i = 0; i < seed.length; i++) {
        h = Math.imul(31, h) + seed.charCodeAt(i) | 0;
    }
    return () => {
        h = Math.imul(1597334677, h);
        return ((h ^ h >>> 15) >>> 0) / 4294967296;
    };
};

export function AgentAvatar({ name, size = 32 }: { name: string; size?: number }) {
    const rand = seededRandom(name);

    // Generate a palette 
    const hues = [250, 150, 30, 290, 200]; // Indigo, Emerald, Orange, Purple, Blue
    const baseHue = hues[Math.floor(rand() * hues.length)];
    const color1 = `hsl(${baseHue}, 80%, 60%)`;
    const color2 = `hsl(${(baseHue + 40) % 360}, 100%, 70%)`;
    const colorBg = `hsl(${baseHue}, 40%, 15%)`;

    // SVG features
    const shapes = [];
    const numShapes = Math.floor(rand() * 3) + 2; // 2 to 4 shapes

    for (let i = 0; i < numShapes; i++) {
        const type = rand() > 0.5 ? 'circle' : 'rect';
        if (type === 'circle') {
            shapes.push(
                <circle
                    key={i}
                    cx={rand() * 24 + 4}
                    cy={rand() * 24 + 4}
                    r={rand() * 8 + 4}
                    fill={rand() > 0.5 ? color1 : color2}
                    opacity={0.8}
                />
            );
        } else {
            shapes.push(
                <rect
                    key={i}
                    x={rand() * 20}
                    y={rand() * 20}
                    width={rand() * 16 + 8}
                    height={rand() * 16 + 8}
                    transform={`rotate(${rand() * 90}, 16, 16)`}
                    fill={rand() > 0.5 ? color1 : color2}
                    opacity={0.8}
                />
            );
        }
    }

    return (
        <svg
            width={size}
            height={size}
            viewBox="0 0 32 32"
            style={{ borderRadius: '8px', background: colorBg, flexShrink: 0 }}
        >
            <defs>
                <filter id="blur" x="-20%" y="-20%" width="140%" height="140%">
                    <feGaussianBlur stdDeviation="2" />
                </filter>
            </defs>

            {/* Background glow base */}
            <circle cx="16" cy="16" r="12" fill={color1} opacity="0.3" filter="url(#blur)" />

            {/* Abstract geometric shapes ("Runes") */}
            <g style={{ mixBlendMode: 'screen' }}>
                {shapes}
            </g>

            {/* Overlay a sharp geometric line indicating "tech/circuit" */}
            <path
                d={`M ${rand() * 10} ${rand() * 10} L ${16 + rand() * 10} ${16 + rand() * 10} L ${rand() * 32} ${32 - rand() * 10}`}
                stroke="rgba(255,255,255,0.6)"
                strokeWidth="1.5"
                fill="none"
            />
        </svg>
    );
}
