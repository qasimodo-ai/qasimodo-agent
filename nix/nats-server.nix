{

  perSystem = {pkgs, ...}: {
    make-shells.default.packages = [ pkgs.nats-server ];
  };
}
