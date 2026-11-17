import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { incident } from "../test/factories";
import { ApprovalPanel } from "./ApprovalPanel";

function setup(onDecide = vi.fn(async () => undefined)) {
  render(<ApprovalPanel incident={incident()} busy={false} onDecide={onDecide} />);
  return { onDecide, user: userEvent.setup() };
}

describe("ApprovalPanel", () => {
  it("starts with every action selected", () => {
    setup();

    expect(screen.getAllByRole("checkbox")).toHaveLength(3);
    screen.getAllByRole("checkbox").forEach((box) => expect(box).toBeChecked());
    expect(screen.getByRole("button", { name: /approve 3 actions/i })).toBeEnabled();
  });

  it("lets the commander narrow the approval and says exactly what will be sent", async () => {
    const { user, onDecide } = setup();

    await user.click(screen.getByLabelText("Roll back abc123"));
    await user.click(screen.getByRole("button", { name: /approve 2 actions/i }));

    const dialog = screen.getByRole("dialog", { name: /approve and hand off/i });
    expect(within(dialog).getByText("Create incident record")).toBeInTheDocument();
    expect(within(dialog).queryByText("Roll back abc123")).not.toBeInTheDocument();
    expect(onDecide).not.toHaveBeenCalled(); // nothing happens until they confirm

    await user.click(within(dialog).getByRole("button", { name: /yes, approve/i }));

    expect(onDecide).toHaveBeenCalledWith({
      decision: "approved",
      approved_actions: ["Create incident record", "Notify the channel"],
    });
  });

  it("cannot approve with nothing selected", async () => {
    const { user } = setup();

    for (const box of screen.getAllByRole("checkbox")) await user.click(box);

    expect(screen.getByRole("button", { name: /approve 0 actions/i })).toBeDisabled();
  });

  it("rejects without sending any actions", async () => {
    const { user, onDecide } = setup();

    await user.click(screen.getByRole("button", { name: /^reject$/i }));
    await user.click(screen.getByRole("button", { name: /yes, reject/i }));

    expect(onDecide).toHaveBeenCalledWith({ decision: "rejected" });
  });

  it("going back from the confirmation does not decide anything", async () => {
    const { user, onDecide } = setup();

    await user.click(screen.getByRole("button", { name: /approve 3 actions/i }));
    await user.click(screen.getByRole("button", { name: /go back/i }));

    expect(onDecide).not.toHaveBeenCalled();
  });
});
