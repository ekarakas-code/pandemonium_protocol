#pragma once

namespace engine {

// Minimal 2D vector used by the motion + physics math. A deliberate distractor family:
// it carries "size"/"length" vocabulary that should NOT outrank the simulation/physics
// queries for their real targets.
struct Vec2 {
    double x{0.0};
    double y{0.0};

    Vec2 add(const Vec2& o) const { return Vec2{x + o.x, y + o.y}; }
    Vec2 scale(double k) const { return Vec2{x * k, y * k}; }
    double length() const { return x * x + y * y; }
};

}  // namespace engine
