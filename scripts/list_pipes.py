"""List all named pipes on the system, filtering for mq-related ones."""
import os

pipe_dir = r'\\.\pipe'
pipes = os.listdir(pipe_dir)

print("All pipes containing 'mq' (case-insensitive):")
for p in sorted(pipes):
    if 'mq' in p.lower():
        print(f"  {p}")

print(f"\nTotal pipes: {len(pipes)}")
