import React from "react";

export function StaggeredGrid({ children }: { children: React.ReactNode }) {
    const childrenArray = React.Children.toArray(children);

    return (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 relative group/grid">
            {childrenArray.map((child, i) => (
                <div
                    key={i}
                    className="animate-in fade-in slide-in-from-bottom-8 duration-1000 fill-mode-both"
                    style={{ animationDelay: `${i * 150 + 500}ms` }}
                >
                    {child}
                </div>
            ))}
        </div>
    );
}
