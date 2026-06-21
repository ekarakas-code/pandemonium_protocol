// Domain-neutral containers, each exposing a `size()` accessor. These are the
// distractors: a compound query like "cell size" collapses onto this `.size()` family
// under a code embedding even though none of them is about grid cells.
#include <cstddef>
#include <string>

/// Fixed-capacity ring buffer of bytes.
struct RingBuffer {
    std::size_t count_;
    /// Number of bytes currently stored in the buffer.
    std::size_t size() const { return count_; }
    /// True when the buffer holds no bytes.
    bool empty() const { return count_ == 0; }
};

/// Growable pool of reusable objects.
struct ObjectPool {
    std::size_t live_;
    /// Number of live objects currently held in the pool.
    std::size_t size() const { return live_; }
};

/// Contiguous array of mesh vertices.
struct VertexArray {
    std::size_t n_;
    /// Number of vertices stored in the array.
    std::size_t size() const { return n_; }
};

/// LIFO stack of parser tokens.
struct TokenStack {
    std::size_t depth_;
    /// Number of tokens on the stack.
    std::size_t size() const { return depth_; }
};

/// FIFO queue of pending events.
struct EventQueue {
    std::size_t pending_;
    /// Number of events waiting in the queue.
    std::size_t size() const { return pending_; }
};

/// Interned string table.
struct StringTable {
    std::size_t entries_;
    /// Number of interned strings in the table.
    std::size_t size() const { return entries_; }
};

/// Fixed-window cache of decoded frames.
struct FrameCache {
    std::size_t held_;
    /// Number of frames currently cached.
    std::size_t size() const { return held_; }
};

/// Singly-linked list of scene nodes.
struct NodeList {
    std::size_t length_;
    /// Number of nodes in the list.
    std::size_t size() const { return length_; }
};
