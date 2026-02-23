import { useEffect, useRef } from 'react';
import { useInView, useSpring } from 'framer-motion';

export function AnimatedNumber({ value }: { value: number }) {
    const ref = useRef<HTMLSpanElement>(null);
    const inView = useInView(ref, { once: true });

    const springValue = useSpring(value, {
        stiffness: 100,
        damping: 30,
        mass: 1,
    });

    useEffect(() => {
        springValue.set(value);
    }, [value, springValue]);

    useEffect(() => {
        if (inView && ref.current) {
            return springValue.on("change", (latest) => {
                if (ref.current) {
                    ref.current.textContent = Intl.NumberFormat("en-US").format(Math.round(latest));
                }
            });
        }
    }, [springValue, inView]);

    return <span ref={ref}>{value}</span>;
}
