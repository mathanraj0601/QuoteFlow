// Shared Framer Motion primitives so timing/feel is identical across the app.
// Import these instead of re-declaring transition objects in each component.

export const spring = { type: "spring", stiffness: 420, damping: 34, mass: 0.9 };
export const springSoft = { type: "spring", stiffness: 260, damping: 30 };
export const ease = [0.22, 1, 0.36, 1]; // easeOutExpo-ish, for tweened fades

// Single element rising into place.
export const fadeUp = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0, transition: { duration: 0.32, ease } },
};

// Wrap a list in this and give each child `variants={fadeUp}` for a staggered reveal.
export const staggerContainer = (stagger = 0.04, delay = 0) => ({
  hidden: {},
  show: { transition: { staggerChildren: stagger, delayChildren: delay } },
});

// Spread onto any <motion.button|a|div> to get a consistent hover-lift + press.
export const pressable = {
  whileHover: { y: -1 },
  whileTap: { scale: 0.97 },
  transition: spring,
};

// Modal / dialog.
export const overlay = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0.18 } },
  exit: { opacity: 0, transition: { duration: 0.15 } },
};

export const sheet = {
  hidden: { opacity: 0, y: 16, scale: 0.97 },
  show: { opacity: 1, y: 0, scale: 1, transition: spring },
  exit: { opacity: 0, y: 8, scale: 0.98, transition: { duration: 0.14 } },
};

// Toast (bottom-center).
export const toastPop = {
  hidden: { opacity: 0, y: 24, scale: 0.96 },
  show: { opacity: 1, y: 0, scale: 1, transition: spring },
  exit: { opacity: 0, y: 12, scale: 0.98, transition: { duration: 0.16 } },
};

// Detail panel / view swap. Kept short and used with mode="popLayout" (not
// "wait") so switching tabs shows the new content immediately instead of
// after a sequential exit-then-enter delay.
export const viewSwap = {
  hidden: { opacity: 0, y: 4 },
  show: { opacity: 1, y: 0, transition: { duration: 0.15, ease } },
  exit: { opacity: 0, y: -3, transition: { duration: 0.1 } },
};
