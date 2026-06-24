#pragma once

#include <cstdint>

namespace engine {

using EntityId = std::uint32_t;

// A bitmask of which components an entity currently carries.
struct ComponentMask {
    std::uint64_t bits{0};
    bool has(unsigned component) const { return (bits >> component) & 1ull; }
    void set(unsigned component) { bits |= (1ull << component); }
};

// A lightweight handle to one simulated entity.
struct Entity {
    EntityId id{0};
    ComponentMask mask;
    bool alive{true};
};

}  // namespace engine
