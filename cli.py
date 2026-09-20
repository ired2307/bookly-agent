"""Entry point — interactive REPL for the Bookly support agent."""
from agent import SupportAgent


def main() -> None:
    agent = SupportAgent()

    print("=" * 56)
    print("  Bookly Customer Support  |  Aria (AI Agent)")
    print("  'new' → reset conversation  |  'quit' → exit")
    print("=" * 56)
    print("\nAria: Hi! Welcome to Bookly support. How can I help you today?\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nAria: Thanks for contacting Bookly. Have a great day!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit"):
            print("\nAria: Thanks for contacting Bookly. Have a great day!")
            break

        if user_input.lower() == "new":
            agent.reset()
            print("\n" + "=" * 56)
            print("  New conversation started")
            print("=" * 56)
            print("\nAria: Hi! How can I help you today?\n")
            continue

        print()
        reply = agent.reply(user_input)
        print(f"Aria: {reply}\n")


if __name__ == "__main__":
    main()
