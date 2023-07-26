from typing import Callable

import torch
import torch.nn as nn

from torchattack.base import Attack


class VNIFGSM(Attack):
    """The VNI-FGSM (Variance-tuned Nesterov Iterative FGSM) attack.

    From the paper 'Enhancing the Transferability of Adversarial Attacks through
    Variance Tuning' https://arxiv.org/abs/2103.15571
    """

    def __init__(
        self,
        model: nn.Module,
        transform: Callable[[torch.Tensor], torch.Tensor] | None,
        eps: float = 8 / 255,
        steps: int = 10,
        alpha: float | None = None,
        decay: float = 1.0,
        n: int = 5,
        beta: float = 1.5,
        clip_min: float = 0.0,
        clip_max: float = 1.0,
        targeted: bool = False,
        device: torch.device | None = None,
    ) -> None:
        """Initialize the VNI-FGSM attack.

        Note:
            Key parameters are `n` and `beta`, where `n` is the number of sampled
            examples for variance tuning and `beta` is the upper bound of the
            neighborhood for varying the perturbation.

        Args:
            model: The model to attack.
            transform: A transform to normalize images.
            eps: The maximum perturbation. Defaults to 8/255.
            steps: Number of steps. Defaults to 10.
            alpha: Step size, `eps / steps` if None. Defaults to None.
            decay: Decay factor for the momentum term. Defaults to 1.0.
            n: Number of sampled examples for variance tuning. Defaults to 5.
            beta: The upper bound of the neighborhood. Defaults to 1.5.
            clip_min: Minimum value for clipping. Defaults to 0.0.
            clip_max: Maximum value for clipping. Defaults to 1.0.
            targeted: Targeted attack if True. Defaults to False.
            device: Device to use for tensors. Defaults to cuda if available.
        """

        super().__init__(transform, device)

        self.model = model
        self.eps = eps
        self.steps = steps
        self.alpha = alpha
        self.decay = decay
        self.n = n
        self.beta = beta
        self.clip_min = clip_min
        self.clip_max = clip_max
        self.targeted = targeted
        self.lossfn = nn.CrossEntropyLoss()

    def forward(self, x: torch.Tensor, y: torch.Tensor) -> torch.Tensor:
        """Perform VNI-FGSM on a batch of images.

        Args:
            x: A batch of images. Shape: (N, C, H, W).
            y: A batch of labels. Shape: (N).

        Returns:
            The perturbed images if successful. Shape: (N, C, H, W).
        """

        g = torch.zeros_like(x)  # Momentum
        v = torch.zeros_like(x)  # Gradient variance
        delta = torch.zeros_like(x, requires_grad=True)

        # If alpha is not given, set to eps / steps
        if self.alpha is None:
            self.alpha = self.eps / self.steps

        # Perform VNI-FGSM
        for _ in range(self.steps):
            # Nesterov gradient component
            nes = self.alpha * self.decay * g
            x_nes = x + delta + nes

            # Compute loss
            outs = self.model(self.transform(x_nes))
            loss = self.lossfn(outs, y)

            if self.targeted:
                loss = -loss

            # Compute gradient
            loss.backward()

            if delta.grad is None:
                continue

            # Apply momentum term and variance
            delta_grad = delta.grad
            g = self.decay * g + (delta_grad + v) / torch.mean(
                torch.abs(delta_grad + v), dim=(1, 2, 3), keepdim=True
            )

            # Compute gradient variance
            gv_grad = torch.zeros_like(x)
            for _ in range(self.n):
                # Get neighboring samples perturbation
                neighbors = delta.data + torch.randn_like(x).uniform_(
                    -self.eps * self.beta, self.eps * self.beta
                )
                neighbors.requires_grad_()
                neighbor_outs = self.model(self.transform(x + neighbors))
                neighbor_loss = self.lossfn(neighbor_outs, y)

                if self.targeted:
                    neighbor_loss = -neighbor_loss

                neighbor_loss.backward()

                gv_grad += neighbors.grad

            # Accumulate gradient variance into v
            v = gv_grad / self.n - delta_grad

            # Update delta
            delta.data = delta.data + self.alpha * g.sign()
            delta.data = torch.clamp(delta.data, -self.eps, self.eps)
            delta.data = torch.clamp(x + delta.data, self.clip_min, self.clip_max) - x

            # Zero out gradient
            delta.grad.detach_()
            delta.grad.zero_()

        return x + delta


if __name__ == "__main__":
    from torchattack.utils import run_attack

    run_attack(VNIFGSM, {"eps": 8 / 255, "steps": 10, "n": 5, "beta": 1.5})
