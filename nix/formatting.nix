{ inputs, ... }:
{
  imports = with inputs; [
    treefmt-nix.flakeModule
    git-hooks.flakeModule
  ];

  perSystem = {
    treefmt = {
      programs = {
        nixfmt = {
          enable = true;
          width = 120;
        };
        nixf-diagnose.enable = true;
        ruff-format = {
          enable = true;
          lineLength = 120;
        };
        ruff-check.enable = true;
        yamlfmt.enable = true;
      };
    };

    pre-commit.settings.hooks.treefmt.enable = true;
  };
}
