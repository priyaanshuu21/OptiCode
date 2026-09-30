// A stand-in for the Monaco worker. Under jsdom there is nothing to run; the point is that the
// module resolves so `new Worker(...)` does not throw.
export default null
